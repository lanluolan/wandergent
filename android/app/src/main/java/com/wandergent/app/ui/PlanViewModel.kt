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
import com.wandergent.app.data.toolFailureText
import com.wandergent.app.data.toolLabel
import com.wandergent.app.data.violationLabel
import com.wandergent.app.data.local.ChatTurnRepository
import com.wandergent.app.data.local.SavedPlanEntity
import com.wandergent.app.data.local.SavedPlanRepository
import com.wandergent.app.data.local.WandergentDatabase
import kotlinx.coroutines.Job
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.takeWhile
import kotlinx.coroutines.launch

/** One tool the agent invoked, and how it went. `ok == null` means still running. */
data class ToolProgress(
    val name: String,
    val subject: String?,
    val ok: Boolean? = null,
    /** Failure kind from the backend; the wording for it is [toolFailureText]. */
    val code: String? = null,
)

/** What the agent is doing right now, accumulated from the event stream. */
data class LiveProgress(
    val stage: String? = null,
    val tools: List<ToolProgress> = emptyList(),
    /** Latest constraint check. Null until it has run; empty list means it passed. */
    val violations: List<Violation>? = null,
    /**
     * The short detail under the progress bar, overwritten by each event. The stage above the
     * bar says which phase the run is in; this says what is happening inside it. A short detail
     * rather than a running log, which pushed the answer itself off the screen.
     */
    val detail: String? = null,
    /** Whether [detail] is reporting something that went wrong, so the UI can colour it. */
    val detailIsProblem: Boolean = false,
)

/** Where one agent reply got to. */
sealed interface TurnState {
    data class Running(val progress: LiveProgress) : TurnState

    /**
     * A result arrived. `response.itinerary` may still be null -- that is the model
     * failing to produce a valid plan, and the UI has to render it as such.
     */
    data class Loaded(val response: PlanResponse) : TurnState

    data class Error(val message: String, val retryable: Boolean) : TurnState

    data object Cancelled : TurnState
}

/**
 * One round of the conversation: what was asked, and what came back.
 *
 * Rounds are **chained**: a message sent while a plan is on screen revises that plan, and
 * the revision is validated and repaired exactly like a first draft. [revision] records
 * which happened, so an edit does not read as a replacement.
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
     * Which local account this conversation belongs to. Set by the screen from the session
     * rather than read here, so there is one source of truth for who is signed in.
     */
    private var owner: Long = SavedPlanEntity.LEGACY_USER

    /**
     * ISO 4217 code the traveller settles up in, set by the screen from their settings.
     * Empty lets the backend use the destination's local currency.
     */
    var currency: String = ""

    private val _transcript = MutableStateFlow<List<Exchange>>(emptyList())
    val transcript: StateFlow<List<Exchange>> = _transcript.asStateFlow()

    private var nextId = 1L
    private var planningJob: Job? = null

    /**
     * Adopt an account and restore its conversation. A follow-up edits the plan above it,
     * so an empty transcript after a restart loses that plan, not just the history.
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
     * Start editing a plan from the library. Seeded as an ordinary exchange rather than a
     * separate "base plan" concept, so send, retry, save and the revision label all work
     * unchanged.
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
     * The newest plan on screen -- what a follow-up edits. Newest rather than the one being
     * replied to: the traveller means the plan they can currently see.
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
     * Re-run one exchange in place. Revisions re-run against the plan that preceded *them*,
     * or retrying an edit would apply it on top of its own output.
     */
    fun retry(id: Long) {
        val exchange = _transcript.value.firstOrNull { it.id == id } ?: return
        if (anyRunning()) return
        if (exchange.state !is TurnState.Error && exchange.state != TurnState.Cancelled) return
        val previous = if (exchange.revision) latestItinerary(before = id) else null
        update(id) {
            it.copy(state = TurnState.Running(LiveProgress(stage = "Connecting")), saved = false)
        }
        run(id, exchange.request, previous)
    }

    fun cancel() {
        val exchange = _transcript.value.firstOrNull { it.state is TurnState.Running } ?: return
        planningJob?.cancel()
        setState(exchange.id, TurnState.Cancelled)
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

    /** Record a finished round. Only on success: a failed run has nothing to revise from. */
    private fun remember(id: Long, response: PlanResponse) {
        if (response.itinerary == null && response.clarification == null) return
        val exchange = _transcript.value.firstOrNull { it.id == id } ?: return
        viewModelScope.launch {
            history.append(owner, exchange.request, response, exchange.revision)
        }
    }

    private fun stateOf(id: Long): TurnState? =
        _transcript.value.firstOrNull { it.id == id }?.state

    private fun constraintsBefore(id: Long) =
        _transcript.value.asSequence()
            .filter { it.id < id }
            .mapNotNull { (it.state as? TurnState.Loaded)?.response }
            .lastOrNull { it.itinerary != null || it.clarification != null }?.constraints

    private fun continuationBefore(id: Long) = _transcript.value.asSequence()
        .filter { it.id < id }
        .mapNotNull { (it.state as? TurnState.Loaded)?.response }
        .lastOrNull()?.continuation

    private fun confirmsWeather(id: Long, message: String): Boolean =
        continuationBefore(id)?.weatherDates?.isNotEmpty() == true &&
            message.trim().trimEnd('.', '!', '。', '！').lowercase() in setOf(
                "yes", "yes please", "yes, continue", "continue", "confirm", "confirmed",
                "continue with seasonal weather", "是", "好的", "可以", "确认", "继续",
                "确认继续", "按季节天气继续",
            )

    private fun run(id: Long, request: String, previous: Itinerary? = null) {
        planningJob = viewModelScope.launch {
            var progress = LiveProgress()
            var events = 0

            try {
                collectPlanEvents(repository.stream(
                    request, currency, previous, constraintsBefore(id),
                    continuationBefore(id), confirmsWeather(id, request),
                )) { event ->
                    currentCoroutineContext().ensureActive()
                    events++
                    progress = reduceProgress(progress, event)
                    when (event.type) {
                        PlanEventDto.RESULT -> {
                            val response = event.result
                            if (response != null) {
                                setState(id, TurnState.Loaded(response))
                                remember(id, response)
                            } else {
                                setState(id, TurnState.Error("No plan was received. Please retry.", true))
                            }
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
                    if (shouldFallBack(events, null)) {
                        fallBackToNonStreaming(id, request, previous)
                    } else {
                        setState(id, TurnState.Error("Connection dropped before the plan was ready. Please retry.", true))
                    }
                }
            } catch (e: StreamFailure) {
                currentCoroutineContext().ensureActive()
                // The transport may not survive SSE -- a proxy buffering the response,
                // most often. See [shouldFallBack] for when a retry is worth it.
                if (shouldFallBack(events, e.status)) {
                    fallBackToNonStreaming(id, request, previous)
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
    ) {
        setState(id, TurnState.Running(LiveProgress(
            stage = "Reconnecting",
            detail = "Trying another connection. You can still stop generation.",
        )))
        // Carries `previous` too: a degraded transport must not silently turn an edit
        // into a from-scratch replan.
        when (val outcome = repository.plan(
            message, currency, previous, constraintsBefore(id),
            continuationBefore(id), confirmsWeather(id, message),
        )) {
            is PlanOutcome.Success -> {
                currentCoroutineContext().ensureActive()
                setState(id, TurnState.Loaded(outcome.response))
                remember(id, outcome.response)
            }
            is PlanOutcome.Failure -> {
                currentCoroutineContext().ensureActive()
                setState(id, TurnState.Error(outcome.message, outcome.retryable))
            }
        }
    }
}

internal suspend fun collectPlanEvents(
    events: Flow<PlanEventDto>,
    onEvent: suspend (PlanEventDto) -> Unit,
) {
    events.takeWhile { event ->
        onEvent(event)
        event.type != PlanEventDto.RESULT && event.type != PlanEventDto.ERROR
    }.collect { }
}

/**
 * Whether a stream that failed should be retried on the plain endpoint.
 *
 * The fallback is for transports that cannot carry SSE, so its signal is that *nothing*
 * arrived -- one event proves the stream works. A 4xx is a refusal, not a transport
 * problem: the plain endpoint runs the same checks and refuses identically, and for a 429
 * the retry spends a second slice of the caller's allowance.
 *
 * A free function, like the reducer below, so a JVM test can reach it.
 */
internal fun shouldFallBack(eventsSeen: Int, status: Int?): Boolean =
    eventsSeen == 0 && status !in 400..499

/**
 * The stream reducer, lifted out of [PlanViewModel] so a JVM test can reach it without
 * constructing Room, DataStore and Retrofit. Everything the progress UI shows is decided
 * here, so this is the piece worth pinning.
 */
internal fun reduceProgress(current: LiveProgress, event: PlanEventDto): LiveProgress =
        when (event.type) {
            PlanEventDto.STAGE -> current.copy(stage = event.message)

            PlanEventDto.TOOL_CALL -> {
                val subject = event.arguments?.get("city")?.toString()?.trim('"')
                current.copy(
                    tools = current.tools + ToolProgress(
                        name = event.name.orEmpty(),
                        subject = subject,
                    ),
                    detail = toolLabel(event.name.orEmpty(), subject),
                    detailIsProblem = false,
                )
            }

            PlanEventDto.TOOL_RESULT -> {
                // Close the *first* still-running call with this name, and only that one:
                // tools run concurrently, so two search_places calls can be open at once
                // and a `map` over the list would close both on the first result.
                val target = current.tools.indexOfFirst {
                    it.name == event.name && it.ok == null
                }
                if (target < 0) {
                    current
                } else {
                    val failed = event.ok == false
                    current.copy(
                        tools = current.tools.mapIndexed { index, tool ->
                            if (index == target) {
                                tool.copy(ok = event.ok, code = event.code)
                            } else {
                                tool
                            }
                        },
                        // A success says nothing new; the line already names the tool. A
                        // failure has to be said -- the plan carries on without it.
                        detail = if (failed) {
                            "${toolLabel(event.name.orEmpty())}: ${toolFailureText(event.code)}"
                        } else {
                            current.detail
                        },
                        // Unchanged on success, so the line keeps its colour.
                        detailIsProblem = failed || current.detailIsProblem,
                    )
                }
            }

            PlanEventDto.COMPOSING -> current.copy(
                detail = "Scheduling ${event.message.orEmpty()}",
                detailIsProblem = false,
            )

            PlanEventDto.VALIDATION -> {
                val found = event.violations.orEmpty()
                val named = found.take(2).joinToString(", ") { violationLabel(it.code) }
                current.copy(
                    violations = found,
                    detail = when {
                        // Verdict first, so a truncated detail still shows the outcome.
                        found.isEmpty() -> "All clear - budget, timing and routing"
                        found.size == 1 -> "Fixing 1 problem: $named"
                        else -> "Fixing ${found.size} problems: $named"
                    },
                    detailIsProblem = found.isNotEmpty(),
                )
            }

            else -> current
        }
