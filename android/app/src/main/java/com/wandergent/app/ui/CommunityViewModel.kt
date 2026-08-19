package com.wandergent.app.ui

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.wandergent.app.data.CommunityOutcome
import com.wandergent.app.data.CommunityRepository
import com.wandergent.app.data.PlanResponse
import com.wandergent.app.data.PublishRequest
import com.wandergent.app.data.SharedPlanCard
import com.wandergent.app.data.SharedPlanDetail
import com.wandergent.app.data.sharedRequest
import com.wandergent.app.data.local.SavedPlanEntity
import com.wandergent.app.data.local.SavedPlanRepository
import com.wandergent.app.data.local.WandergentDatabase
import kotlinx.coroutines.FlowPreview
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.debounce
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.drop
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class CommunityState(
    val loading: Boolean = false,
    val cards: List<SharedPlanCard> = emptyList(),
    val error: String? = null,
    /** Set once a load has completed, so an empty feed is not shown before the first one. */
    val loaded: Boolean = false,
    /** Where the next page starts. Null means there is nothing more to fetch. */
    val nextCursor: String? = null,
    /** A page after the first is in flight; the list stays put and a spinner sits below. */
    val loadingMore: Boolean = false,
) {
    val hasMore: Boolean get() = nextCursor != null
}

/** Long enough to finish a word, short enough not to feel laggy. */
private const val SEARCH_DEBOUNCE_MS = 350L

@OptIn(FlowPreview::class)
class CommunityViewModel(application: Application) : AndroidViewModel(application) {

    private val repository = CommunityRepository()
    private val library = SavedPlanRepository(
        WandergentDatabase.get(application).savedPlanDao(),
    )

    private val _state = MutableStateFlow(CommunityState())
    val state: StateFlow<CommunityState> = _state.asStateFlow()

    /** The full plan behind an expanded card, fetched on demand and kept while open. */
    private val _opened = MutableStateFlow<Map<String, SharedPlanDetail>>(emptyMap())
    val opened: StateFlow<Map<String, SharedPlanDetail>> = _opened.asStateFlow()

    private val _message = MutableStateFlow<String?>(null)
    val message: StateFlow<String?> = _message.asStateFlow()

    /** Which local row owns the copied trips. A storage key, not an identity. */
    private var userId: Long? = null

    /**
     * The server account id, which is what a post's `author_id` is compared against.
     *
     * Two ids, and conflating them was a live bug: `isMine` compared the *local* row id
     * against an author id that had become a server uuid, so the reader's own posts
     * showed a save button and a legacy post whose author id happened to be "1" showed a
     * delete button. They are different namespaces and have to stay separate.
     */
    private var accountId: String? = null

    /**
     * What the reader is searching for.
     *
     * Debounced rather than searched per keystroke: "New Orleans" is eleven requests
     * otherwise, and the ten that are thrown away can still arrive out of order and
     * overwrite the one that matters.
     *
     * **Declared above the `init` block that collects it, and that is load-bearing.**
     * Kotlin runs property initialisers and init blocks in declaration order, so an init
     * block reading a property declared below it sees null -- which crashed the app on
     * launch, inside `debounce`, with a stack trace naming neither this class nor the
     * field.
     */
    private val _query = MutableStateFlow("")
    val query: StateFlow<String> = _query.asStateFlow()

    init {
        viewModelScope.launch {
            // `drop(1)` skips the initial empty value, so opening the tab does not fire a
            // second load on top of the one the screen already asked for.
            _query.drop(1).debounce(SEARCH_DEBOUNCE_MS).distinctUntilChanged().collect {
                refresh()
            }
        }
    }

    /**
     * Tell the feed who is reading. Does not load -- see [refresh].
     *
     * Split apart because a feed goes stale by *time*, not by the reader changing: while
     * this was "set the id, and reload only when it differs", a trip someone else posted
     * while the app was open stayed invisible until the refresh button was tapped.
     */
    fun setUser(id: Long?, accountId: String?) {
        userId = id
        this.accountId = accountId?.takeIf { it.isNotEmpty() }
    }

    fun refresh() {
        // One in flight is enough. Entering the tab and a publish completing can land
        // together, and two identical requests would race to set the same list.
        if (_state.value.loading) return
        _state.update { it.copy(loading = true, error = null) }
        viewModelScope.launch {
            when (val outcome = repository.feed(destination = _query.value.trim())) {
                is CommunityOutcome.Success ->
                    _state.value = CommunityState(
                        cards = outcome.value.items,
                        loaded = true,
                        nextCursor = outcome.value.nextCursor,
                    )
                is CommunityOutcome.Failure ->
                    _state.update {
                        // Keep whatever is already on screen. A feed that empties itself
                        // because the tunnel dropped reads as "everyone deleted their
                        // trips", which is a worse lie than a stale list with a banner.
                        it.copy(loading = false, error = outcome.message, loaded = true)
                    }
            }
        }
    }

    /**
     * Fetch the page after the one on screen.
     *
     * Guarded on both flags: the list scrolls while a page is in flight, so the "you have
     * reached the end" trigger fires repeatedly and would otherwise request the same
     * cursor several times over.
     */
    fun loadMore() {
        val state = _state.value
        val cursor = state.nextCursor ?: return
        if (state.loading || state.loadingMore) return

        _state.update { it.copy(loadingMore = true) }
        viewModelScope.launch {
            when (val outcome = repository.feed(cursor = cursor, destination = _query.value.trim())) {
                is CommunityOutcome.Success -> _state.update {
                    // Appended to whatever is on screen *now*, and de-duplicated by id: a
                    // refresh can land between asking and answering, and a plan withdrawn
                    // in between shifts the window.
                    val seen = it.cards.mapTo(mutableSetOf()) { card -> card.id }
                    it.copy(
                        cards = it.cards + outcome.value.items.filter { card -> card.id !in seen },
                        nextCursor = outcome.value.nextCursor,
                        loadingMore = false,
                    )
                }
                is CommunityOutcome.Failure ->
                    _state.update { it.copy(loadingMore = false, error = outcome.message) }
            }
        }
    }

    fun search(text: String) {
        _query.value = text
    }

    /** Pull the full itinerary for a card the reader just expanded. */
    fun open(id: String) {
        if (_opened.value.containsKey(id)) return
        viewModelScope.launch {
            when (val outcome = repository.plan(id)) {
                is CommunityOutcome.Success -> _opened.update { it + (id to outcome.value) }
                is CommunityOutcome.Failure -> _message.value = outcome.message
            }
        }
    }

    /**
     * Save or unsave someone else's trip.
     *
     * Two things happen, and only one of them is the count. Saving also copies the
     * itinerary into this account's own library, because "saved" has meant *that* since
     * the library existed, and a heart that only moved a number on a stranger's card
     * would be a different feature wearing the same icon.
     */
    fun toggleSave(card: SharedPlanCard) {
        val user = userId ?: run {
            _message.value = "Sign in to save trips."
            return
        }
        val wanted = !(card.savedByViewer ?: false)

        viewModelScope.launch {
            when (val outcome = repository.setSaved(card.id, wanted)) {
                is CommunityOutcome.Failure -> _message.value = outcome.message
                is CommunityOutcome.Success -> {
                    _state.update { current ->
                        current.copy(
                            cards = current.cards.map {
                                if (it.id == card.id) {
                                    it.copy(
                                        savedByViewer = outcome.value.saved,
                                        saveCount = outcome.value.saveCount,
                                    )
                                } else {
                                    it
                                }
                            }
                        )
                    }
                    if (outcome.value.saved) copyToLibrary(card, user)
                }
            }
        }
    }

    private suspend fun copyToLibrary(card: SharedPlanCard, user: Long) {
        val detail = _opened.value[card.id] ?: when (val fetched = repository.plan(card.id)) {
            is CommunityOutcome.Success -> fetched.value
            is CommunityOutcome.Failure -> {
                _message.value = "Saved to the feed, but the copy failed: ${fetched.message}"
                return
            }
        }
        library.save(
            userId = user,
            request = detail.request.ifBlank { "Shared by ${detail.authorName}" },
            response = PlanResponse(itinerary = detail.itinerary),
            sharedPlanId = detail.id,
        )
        _message.value = "Saved to your library."
    }

    /**
     * Share one of this account's own saved trips.
     *
     * `includeRequest` decides whether the original free-text request goes with it. It
     * defaults to false at the call site for privacy reasons -- see [sharedRequest].
     */
    fun publish(
        plan: SavedPlanEntity,
        authorName: String,
        note: String,
        includeRequest: Boolean,
    ) {
        val user = userId ?: run {
            _message.value = "Sign in to share trips."
            return
        }
        val itinerary = library.decode(plan)?.itinerary ?: run {
            _message.value = "That saved trip could not be read, so it was not shared."
            return
        }
        viewModelScope.launch {
            val outcome = repository.publish(
                PublishRequest(
                    request = sharedRequest(plan.request, includeRequest),
                    note = note,
                    itinerary = itinerary,
                )
            )
            when (outcome) {
                is CommunityOutcome.Success -> {
                    _message.value = "Shared to the community."
                    refresh()
                }
                is CommunityOutcome.Failure -> _message.value = outcome.message
            }
        }
    }

    /** Take one of your own posts down. */
    fun withdraw(card: SharedPlanCard) {
        val user = userId ?: return
        viewModelScope.launch {
            when (val outcome = repository.withdraw(card.id)) {
                is CommunityOutcome.Success -> {
                    _state.update { it.copy(cards = it.cards.filterNot { c -> c.id == card.id }) }
                    _message.value = "Taken down."
                }
                is CommunityOutcome.Failure -> _message.value = outcome.message
            }
        }
    }

    fun clearMessage() {
        _message.value = null
    }

    /** True when this card is the signed-in account's own post. */
    fun isMine(card: SharedPlanCard): Boolean = ownsCard(accountId, card)
}

/**
 * Whether the signed-in account wrote this post, lifted out of [CommunityViewModel] to be
 * testable.
 *
 * The comparison is deliberately narrow. `authorId` is the **server** account id; the app also
 * carries a local Room row id, and comparing the two was a live bug that showed other people's
 * posts as the reader's own, complete with a withdraw button. A blank account id is not a match
 * either: signed out is not "everything is mine".
 */
internal fun ownsCard(accountId: String?, card: SharedPlanCard): Boolean =
    !accountId.isNullOrEmpty() && accountId == card.authorId
