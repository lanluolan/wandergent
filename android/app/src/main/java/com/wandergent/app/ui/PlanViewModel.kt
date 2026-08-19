package com.wandergent.app.ui

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.wandergent.app.data.Itinerary
import com.wandergent.app.data.PlanEventDto
import com.wandergent.app.data.PlanOutcome
import com.wandergent.app.data.PlanRepository
import com.wandergent.app.data.PlanResponse
import com.wandergent.app.data.StreamFailure
import com.wandergent.app.data.Violation
import com.wandergent.app.data.describeFailure
import com.wandergent.app.data.local.ChatTurnRepository
import com.wandergent.app.data.local.SavedPlanEntity
import com.wandergent.app.data.local.SavedPlanRepository
import com.wandergent.app.data.local.WandergentDatabase
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/** One tool the agent invoked, and how it went. `ok == null` means still running. */
data class ToolProgress(
    val name: String,
    val subject: String?,
    val ok: Boolean? = null,
    val error: String? = null,
)

/** What the agent is doing right now, accumulated from the event stream. */
data class LiveProgress(
    val stage: String? = null,
    val tools: List<ToolProgress> = emptyList(),
    val writing: List<String> = emptyList(),
    /** Latest constraint check. Null until it has run; empty list means it passed. */
    val violations: List<Violation>? = null,
) {
    companion object {
        /** Enough to show momentum without turning the screen into a log. */
        const val MAX_WRITING = 6
    }
}

/** Where one agent reply got to. */
sealed interface TurnState {
    data class Running(val progress: LiveProgress) : TurnState

    /**
     * A result arrived. `response.itinerary` may still be null -- that is the model
     * failing to produce a valid plan, and the UI has to render it as such.
     */
    data class Loaded(val response: PlanResponse) : TurnState

    data class Error(val message: String, val retryable: Boolean) : TurnState
}

/**
 * One round of the conversation: what was asked, and what came back.
 *
 * Rounds are **chained**: a message sent while a plan is already on screen revises that
 * plan rather than starting over, and the revision is validated and repaired exactly
 * like a first draft. [revision] records which happened, because "I changed your day 2"
 * and "here is a whole new trip" must not look the same on screen.
 */
data class Exchange(
    val id: Long,
    val request: String,
    val state: TurnState,
    val saved: Boolean = false,
    val revision: Boolean = false,
)

/**
 * Id for a plan pulled in from the library.
 *
 * Negative so it cannot collide with a Room row id, and fixed so seeding twice is a
 * no-op rather than a pile of duplicates.
 */
private const val SAVED_SEED_ID = -1L

class PlanViewModel(application: Application) : AndroidViewModel(application) {

    private val repository = PlanRepository()
    private val database = WandergentDatabase.get(application)
    private val savedPlans = SavedPlanRepository(database.savedPlanDao())
    private val history = ChatTurnRepository(database.chatTurnDao())

    /**
     * Which local account this conversation belongs to.
     *
     * Set by the screen from the session rather than read here: this view model is
     * scoped to the plan tab, and reaching into auth state from it would create a
     * second source of truth for who is signed in.
     */
    private var owner: Long = SavedPlanEntity.LEGACY_USER

    /** The backend takes the id as a string; empty stays anonymous. */
    private val userId: String
        get() = if (owner == SavedPlanEntity.LEGACY_USER) "" else owner.toString()

    /**
     * ISO 4217 code the traveller settles up in, set by the screen from their settings.
     * Empty lets the backend use the destination's local currency.
     */
    var currency: String = ""

    private val _transcript = MutableStateFlow<List<Exchange>>(emptyList())
    val transcript: StateFlow<List<Exchange>> = _transcript.asStateFlow()

    private var nextId = 1L

    /**
     * Adopt an account and restore its conversation.
     *
     * Restoring matters more than it looks: a follow-up edits the plan above it, so an
     * empty transcript after a restart does not just lose history -- it loses the plan
     * the user was about to change.
     */
    fun setUser(id: Long?) {
        val next = id ?: SavedPlanEntity.LEGACY_USER
        if (owner == next && _transcript.value.isNotEmpty()) return
        owner = next
        viewModelScope.launch {
            val restored = history.restore(next).map {
                Exchange(
                    id = it.id,
                    request = it.request,
                    state = TurnState.Loaded(it.response),
                    revision = it.revision,
                )
            }
            // Only adopt the restored turns if nothing has happened since -- a user who
            // typed straight away must not have their run replaced by history.
            if (_transcript.value.isEmpty()) {
                _transcript.value = restored
                nextId = (restored.maxOfOrNull { it.id } ?: 0L) + 1
            }
        }
    }

    /**
     * Start editing a plan from the library.
     *
     * Seeds it as an ordinary exchange rather than inventing a separate "base plan"
     * concept: once it is in the transcript, [latestItinerary] picks it up and every
     * existing path -- send, retry, save, the revision label -- works unchanged.
     */
    fun reviseSaved(plan: SavedPlanEntity) {
        val response = savedPlans.decode(plan) ?: return
        if (response.itinerary == null) return
        if (_transcript.value.any { it.id == SAVED_SEED_ID }) return

        _transcript.value = _transcript.value + Exchange(
            id = SAVED_SEED_ID,
            request = plan.request,
            state = TurnState.Loaded(response),
            saved = true,
            revision = false,
        )
    }

    /** Forget this conversation and start over. The library is untouched. */
    fun startNewConversation() {
        if (anyRunning()) return
        _transcript.value = emptyList()
        nextId = 1L
        viewModelScope.launch { history.clear(owner) }
    }

    /** One run at a time: two concurrent plans would interleave in the same transcript. */
    private fun anyRunning() = _transcript.value.any { it.state is TurnState.Running }

    /**
     * The newest plan on screen -- what a follow-up message edits.
     *
     * The newest rather than the one being replied to: after three rounds of changes
     * the traveller means the plan they can currently see, not the first draft.
     */
    private fun latestItinerary(before: Long? = null): Itinerary? = _transcript.value
        .asSequence()
        .filter { before == null || it.id < before }
        .mapNotNull { (it.state as? TurnState.Loaded)?.response?.itinerary }
        .lastOrNull()

    fun send(message: String) {
        val trimmed = message.trim()
        if (trimmed.isEmpty() || anyRunning()) return

        val previous = latestItinerary()
        val id = nextId++
        _transcript.value = _transcript.value + Exchange(
            id = id,
            request = trimmed,
            state = TurnState.Running(LiveProgress(stage = "Connecting")),
            revision = previous != null,
        )
        run(id, trimmed, previous)
    }

    /**
     * Re-run one exchange in place, keeping its position in the conversation.
     *
     * Revisions re-run against the plan that preceded *them*, not the newest one --
     * otherwise retrying an edit would apply it on top of its own output.
     */
    fun retry(id: Long) {
        val exchange = _transcript.value.firstOrNull { it.id == id } ?: return
        if (anyRunning()) return
        val previous = if (exchange.revision) latestItinerary(before = id) else null
        update(id) {
            it.copy(state = TurnState.Running(LiveProgress(stage = "Connecting")), saved = false)
        }
        run(id, exchange.request, previous)
    }

    fun save(id: Long) {
        val exchange = _transcript.value.firstOrNull { it.id == id } ?: return
        val loaded = exchange.state as? TurnState.Loaded ?: return
        if (exchange.saved) return
        viewModelScope.launch {
            if (savedPlans.save(owner, exchange.request, loaded.response)) {
                update(id) { it.copy(saved = true) }
            }
        }
    }

    private fun update(id: Long, transform: (Exchange) -> Exchange) {
        _transcript.value = _transcript.value.map { if (it.id == id) transform(it) else it }
    }

    private fun setState(id: Long, state: TurnState) = update(id) { it.copy(state = state) }

    /**
     * Record a finished round so it survives the process.
     *
     * Only on success: a failed run has nothing to revise from, and restoring an error
     * on next launch would be noise rather than history.
     */
    private fun remember(id: Long, response: PlanResponse) {
        if (response.itinerary == null) return
        val exchange = _transcript.value.firstOrNull { it.id == id } ?: return
        viewModelScope.launch {
            history.append(owner, exchange.request, response, exchange.revision)
        }
    }

    private fun stateOf(id: Long): TurnState? =
        _transcript.value.firstOrNull { it.id == id }?.state

    private fun run(id: Long, request: String, previous: Itinerary? = null) {
        viewModelScope.launch {
            var progress = LiveProgress()
            var events = 0

            try {
                repository.stream(request, currency, previous).collect { event ->
                    events++
                    progress = reduce(progress, event)
                    when (event.type) {
                        PlanEventDto.RESULT -> event.result?.let {
                            setState(id, TurnState.Loaded(it))
                            remember(id, it)
                        }
                        PlanEventDto.ERROR -> setState(
                            id,
                            TurnState.Error(event.message ?: "Could not generate a plan", retryable = true),
                        )
                        else -> setState(id, TurnState.Running(progress))
                    }
                }
                // The stream closed without a terminal event; nothing was decided.
                if (stateOf(id) is TurnState.Running) {
                    fallBackToNonStreaming(id, request, previous, "Connection dropped")
                }
            } catch (e: StreamFailure) {
                // Nothing arrived at all: the transport may not survive SSE (a proxy
                // buffering the response, for instance), so try the plain endpoint.
                if (events == 0) {
                    fallBackToNonStreaming(id, request, previous, e.message ?: "Streaming failed")
                } else {
                    val failure = describeFailure(e.status, e.detail)
                    setState(id, TurnState.Error(failure.message, failure.retryable))
                }
            }
        }
    }

    private suspend fun fallBackToNonStreaming(
        id: Long,
        message: String,
        previous: Itinerary?,
        reason: String,
    ) {
        setState(id, TurnState.Running(LiveProgress(stage = "$reason -- retrying without streaming")))
        // The fallback carries the plan being revised too: degrading the transport must
        // not silently degrade an edit into a from-scratch replan.
        when (val outcome = repository.plan(message, currency, previous)) {
            is PlanOutcome.Success -> {
                setState(id, TurnState.Loaded(outcome.response))
                remember(id, outcome.response)
            }
            is PlanOutcome.Failure ->
                setState(id, TurnState.Error(outcome.message, outcome.retryable))
        }
    }

    /** Fold one event into the live progress shown while the plan is being built. */
    private fun reduce(current: LiveProgress, event: PlanEventDto): LiveProgress =
        when (event.type) {
            PlanEventDto.STAGE -> current.copy(stage = event.message)

            PlanEventDto.TOOL_CALL -> current.copy(
                tools = current.tools + ToolProgress(
                    name = event.name.orEmpty(),
                    subject = event.arguments?.get("city")?.toString()?.trim('"'),
                ),
            )

            PlanEventDto.TOOL_RESULT -> current.copy(
                tools = current.tools.map { tool ->
                    // Match the first still-running call with this name: tools run
                    // concurrently, so results can arrive out of order.
                    if (tool.name == event.name && tool.ok == null) {
                        tool.copy(ok = event.ok, error = event.error)
                    } else {
                        tool
                    }
                },
            )

            PlanEventDto.COMPOSING -> current.copy(
                writing = (current.writing + event.message.orEmpty())
                    .takeLast(LiveProgress.MAX_WRITING),
            )

            PlanEventDto.VALIDATION -> current.copy(violations = event.violations.orEmpty())

            else -> current
        }
}
