package com.wandergent.app.ui

import com.wandergent.app.data.PlanEventDto
import com.wandergent.app.data.Violation
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull
import kotlin.test.assertTrue

/**
 * The live-progress reducer: everything the screen shows while a plan is being built.
 *
 * This layer had no tests and produced every recent bug, because the view models construct
 * Room, DataStore and Retrofit in their constructors and cannot be built in a JVM test. The
 * reducer is pure, so lifting it out was enough to reach it. What remains untestable is the
 * orchestration around it.
 */
class LiveProgressTest {

    private fun stage(message: String) = PlanEventDto(type = PlanEventDto.STAGE, message = message)
    private fun call(name: String) = PlanEventDto(type = PlanEventDto.TOOL_CALL, name = name)
    private fun result(name: String, ok: Boolean, error: String? = null) =
        PlanEventDto(type = PlanEventDto.TOOL_RESULT, name = name, ok = ok, error = error)

    private fun fold(vararg events: PlanEventDto): LiveProgress =
        events.fold(LiveProgress()) { acc, e -> reduceProgress(acc, e) }

    @Test
    fun `a stage replaces the previous one rather than accumulating`() {
        val progress = fold(stage("Understanding your request"), stage("Building the itinerary"))

        assertEquals("Building the itinerary", progress.stage)
    }

    @Test
    fun `a tool call arrives as still running`() {
        val progress = fold(call("get_weather_forecast"))

        val tool = progress.tools.single()
        assertEquals("get_weather_forecast", tool.name)
        // `ok == null` is what the UI renders as a spinner. A default of false would show
        // every in-flight call as already failed.
        assertNull(tool.ok)
    }

    @Test
    fun `a result closes the first still-running call of that name`() {
        // Tools run concurrently, so two search_places calls can be open at once and their
        // results can come back in either order. Closing the *first* open one keeps the count
        // right; matching by name alone would overwrite an already-finished call and leave a
        // second one spinning forever.
        val progress = fold(
            call("search_places"),
            call("search_places"),
            result("search_places", ok = true),
        )

        assertEquals(2, progress.tools.size)
        assertEquals(true, progress.tools[0].ok)
        assertNull(progress.tools[1].ok)
    }

    @Test
    fun `a second result closes the second call`() {
        val progress = fold(
            call("search_places"),
            call("search_places"),
            result("search_places", ok = true),
            result("search_places", ok = false, error = "timed out"),
        )

        assertEquals(listOf(true, false), progress.tools.map { it.ok })
        assertEquals("timed out", progress.tools[1].error)
    }

    @Test
    fun `a result for a name that was never called changes nothing`() {
        val progress = fold(call("get_weather_forecast"), result("search_places", ok = true))

        assertEquals(1, progress.tools.size)
        assertNull(progress.tools.single().ok)
    }

    @Test
    fun `place names accumulate but are capped`() {
        // The cap is why the screen shows momentum instead of turning into a log.
        val events = (1..LiveProgress.MAX_WRITING + 4).map {
            PlanEventDto(type = PlanEventDto.COMPOSING, message = "place $it")
        }

        val progress = fold(*events.toTypedArray())

        assertEquals(LiveProgress.MAX_WRITING, progress.writing.size)
        // The newest survive, not the oldest: this is a running view, not a transcript.
        assertEquals("place ${LiveProgress.MAX_WRITING + 4}", progress.writing.last())
        assertEquals("place 5", progress.writing.first())
    }

    @Test
    fun `validation replaces the finding list instead of appending to it`() {
        val first = PlanEventDto(
            type = PlanEventDto.VALIDATION,
            violations = listOf(Violation(code = "over_budget", message = "over by 200")),
        )
        val second = PlanEventDto(type = PlanEventDto.VALIDATION, violations = emptyList())

        val progress = fold(first, second)

        // A repair round re-validates, and the second report is the truth. Appending would
        // leave a repaired plan showing the problem it just fixed.
        assertEquals(emptyList(), progress.violations)
    }

    @Test
    fun `violations stay null until validation has actually run`() {
        // Null and empty mean different things: "not checked yet" versus "checked, and clean".
        assertNull(fold(stage("Understanding your request")).violations)
        assertTrue(fold(PlanEventDto(type = PlanEventDto.VALIDATION)).violations!!.isEmpty())
    }

    @Test
    fun `an event type this build has never heard of is ignored`() {
        // The server ships ahead of the app; a new event kind must not blank the screen.
        val progress = fold(stage("Building the itinerary"), PlanEventDto(type = "invented_later"))

        assertEquals("Building the itinerary", progress.stage)
    }
}
