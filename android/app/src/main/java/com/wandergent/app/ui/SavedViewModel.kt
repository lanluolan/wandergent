package com.wandergent.app.ui

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.wandergent.app.data.PlanResponse
import com.wandergent.app.data.local.SavedPlanEntity
import com.wandergent.app.data.local.SavedPlanRepository
import com.wandergent.app.data.local.WandergentDatabase
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.flatMapLatest
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

/** A deletion the user can still take back. */
data class PendingUndo(val plan: SavedPlanEntity)

class SavedViewModel(application: Application) : AndroidViewModel(application) {

    private val repository = SavedPlanRepository(
        WandergentDatabase.get(application).savedPlanDao(),
    )

    private val userId = MutableStateFlow<Long?>(null)

    @OptIn(ExperimentalCoroutinesApi::class)
    val plans: StateFlow<List<SavedPlanEntity>> = userId
        .flatMapLatest { id -> if (id == null) flowOf(emptyList()) else repository.observeFor(id) }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), emptyList())

    /** Set once the session is known; also claims any pre-account rows for this user. */
    fun setUser(id: Long?) {
        if (userId.value == id) return
        userId.value = id
        if (id != null) viewModelScope.launch { repository.adoptLegacy(id) }
    }

    private val _undo = MutableStateFlow<PendingUndo?>(null)
    val undo: StateFlow<PendingUndo?> = _undo.asStateFlow()

    /**
     * Delete, but keep the row in hand so it can be put back.
     *
     * A confirmation catches the mis-tap before it happens and the undo catches the
     * confirmed one the user regrets a second later. Neither covers the other case.
     */
    fun delete(plan: SavedPlanEntity) {
        viewModelScope.launch {
            repository.delete(plan)
            _undo.value = PendingUndo(plan)
        }
    }

    fun undoDelete() {
        val pending = _undo.value ?: return
        _undo.value = null
        viewModelScope.launch { repository.restore(pending.plan) }
    }

    /** The offer expired: the snackbar went away and the deletion stands. */
    fun clearUndo() {
        _undo.value = null
    }

    fun decode(plan: SavedPlanEntity): PlanResponse? = repository.decode(plan)
}
