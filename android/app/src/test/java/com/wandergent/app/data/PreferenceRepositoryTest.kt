package com.wandergent.app.data

import java.io.IOException
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.runBlocking
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertIs

class PreferenceRepositoryTest {
    private class FakeApi : PreferenceApi {
        val calls = mutableListOf<String>()
        var failure: Exception? = null
        override suspend fun list(authorization: String): List<SavedPreference> {
            calls += authorization
            failure?.let { throw it }
            return emptyList()
        }
        override suspend fun delete(id: String, authorization: String) {
            calls += "$authorization/$id"
            failure?.let { throw it }
        }
    }

    @Test
    fun `management sends its captured session on reads and deletes`() = runBlocking<Unit> {
        val api = FakeApi()
        val repository = PreferenceRepository(api, "session-a")
        repository.list()
        repository.delete("preference-id")
        assertEquals(listOf("Bearer session-a", "Bearer session-a/preference-id"), api.calls)
    }

    @Test
    fun `signed out never makes an anonymous management request`() = runBlocking<Unit> {
        val api = FakeApi()
        val repository = PreferenceRepository(api, null)
        assertIs<PreferenceOutcome.Failure>(repository.list())
        assertIs<PreferenceOutcome.Failure>(repository.delete("preference-id"))
        assertEquals(emptyList(), api.calls)
    }

    @Test
    fun `failed delete is not reported as success`() = runBlocking<Unit> {
        val api = FakeApi().apply { failure = IOException("offline") }
        assertIs<PreferenceOutcome.Failure>(PreferenceRepository(api, "session").delete("id"))
    }

    @Test
    fun `leaving the screen cancels instead of producing an error`() = runBlocking<Unit> {
        val api = FakeApi().apply { failure = CancellationException() }
        assertFailsWith<CancellationException> { PreferenceRepository(api, "session").list() }
    }

    @Test
    fun `server preference payload parses without dropping its identity`() {
        val decoded = ApiJson.json.decodeFromString<List<SavedPreference>>(
            """[{"id":"abc","text":"prefers museums","created_at":"2026-09-06T12:00:00Z"}]"""
        )
        assertEquals(SavedPreference("abc", "prefers museums", "2026-09-06T12:00:00Z"), decoded.single())
    }
}
