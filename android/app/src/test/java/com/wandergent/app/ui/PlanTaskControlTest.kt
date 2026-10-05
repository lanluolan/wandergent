package com.wandergent.app.ui

import com.wandergent.app.data.PlanEventDto
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking

class PlanTaskControlTest {
    @Test
    fun `result stops collection and closes upstream before later events`() = runBlocking {
        assertTerminalClosesStream(PlanEventDto.RESULT)
    }

    @Test
    fun `error stops collection and closes upstream before later events`() = runBlocking {
        assertTerminalClosesStream(PlanEventDto.ERROR)
    }

    @Test
    fun `cancelling a running task closes its stream without requesting fallback`() = runBlocking {
        val started = CompletableDeferred<Unit>()
        var closed = false
        var fallback = false
        val task = launch {
            collectPlanEvents(flow {
                try {
                    emit(PlanEventDto(type = PlanEventDto.STAGE, message = "Planning"))
                    started.complete(Unit)
                    awaitCancellation()
                } finally {
                    closed = true
                }
            }) { }
            fallback = true
        }
        started.await()
        task.cancelAndJoin()
        assertTrue(closed)
        assertFalse(fallback)
    }

    @Test
    fun `clean disconnect after progress requires manual retry`() = runBlocking {
        var events = 0
        collectPlanEvents(flowOf(PlanEventDto(type = PlanEventDto.STAGE))) { events++ }
        assertFalse(shouldFallBack(events, null))
    }

    @Test
    fun `empty stream can use the connection fallback`() = runBlocking {
        var events = 0
        collectPlanEvents(flowOf()) { events++ }
        assertTrue(shouldFallBack(events, null))
    }

    private suspend fun assertTerminalClosesStream(type: String) {
        val received = mutableListOf<String>()
        var closed = false
        var continued = false
        collectPlanEvents(flow {
            try {
                emit(PlanEventDto(type = PlanEventDto.STAGE))
                emit(PlanEventDto(type = type))
                continued = true
                emit(PlanEventDto(type = PlanEventDto.STAGE))
            } finally {
                closed = true
            }
        }) { received += it.type }
        assertEquals(listOf(PlanEventDto.STAGE, type), received)
        assertTrue(closed)
        assertFalse(continued)
    }
}
