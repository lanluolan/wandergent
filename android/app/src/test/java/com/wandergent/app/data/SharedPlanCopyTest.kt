package com.wandergent.app.data

import com.wandergent.app.data.local.SavedPlanDao
import com.wandergent.app.data.local.SavedPlanEntity
import com.wandergent.app.data.local.SavedPlanRepository
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.runBlocking

/**
 * Saving a community trip into the library has to be idempotent, and it was not.
 *
 * The server's save is keyed on `(plan_id, user_id)`, so pressing the heart twice counts
 * once there. The local copy had no such key, so **save -> unsave -> save left two
 * identical trips in the library**. Found by reading the code back rather than by using
 * the app, which is why it is pinned here.
 *
 * Room needs a device, so this drives the repository against a fake DAO -- the rule being
 * tested lives in the repository, not in SQL.
 */
class SharedPlanCopyTest {

    private class FakeDao : SavedPlanDao {
        val rows = mutableListOf<SavedPlanEntity>()
        private var nextId = 1L

        override fun observeFor(userId: Long): Flow<List<SavedPlanEntity>> =
            flowOf(rows.filter { it.userId == userId })

        override suspend fun adoptLegacy(userId: Long): Int = 0

        override suspend fun insert(plan: SavedPlanEntity): Long {
            val id = nextId++
            rows += plan.copy(id = id)
            return id
        }

        override suspend fun findShared(userId: Long, sharedPlanId: String): SavedPlanEntity? =
            rows.firstOrNull { it.userId == userId && it.sharedPlanId == sharedPlanId }

        override suspend fun delete(plan: SavedPlanEntity) {
            rows.removeAll { it.id == plan.id }
        }
    }

    private val plan = PlanResponse(
        itinerary = Itinerary(
            destination = "New Orleans",
            startDate = "2026-10-02",
            endDate = "2026-10-03",
            currency = "USD",
            days = emptyList(),
        ),
    )

    @Test
    fun `saving the same shared trip twice keeps one copy`() = runBlocking {
        val dao = FakeDao()
        val repository = SavedPlanRepository(dao)

        repository.save(userId = 1, request = "r", response = plan, sharedPlanId = "post-1")
        repository.save(userId = 1, request = "r", response = plan, sharedPlanId = "post-1")

        assertEquals(1, dao.rows.size)
    }

    @Test
    fun `a different post is a different trip`() = runBlocking {
        val dao = FakeDao()
        val repository = SavedPlanRepository(dao)

        repository.save(userId = 1, request = "r", response = plan, sharedPlanId = "post-1")
        repository.save(userId = 1, request = "r", response = plan, sharedPlanId = "post-2")

        assertEquals(2, dao.rows.size)
    }

    @Test
    fun `two accounts each get their own copy`() = runBlocking {
        val dao = FakeDao()
        val repository = SavedPlanRepository(dao)

        repository.save(userId = 1, request = "r", response = plan, sharedPlanId = "post-1")
        repository.save(userId = 2, request = "r", response = plan, sharedPlanId = "post-1")

        assertEquals(2, dao.rows.size)
    }

    @Test
    fun `deleting your copy and saving again gets it back`() = runBlocking {
        // Why the check is on the post's identity and not on the trip's contents: this
        // is a legitimate second copy, and a contents match would refuse it.
        val dao = FakeDao()
        val repository = SavedPlanRepository(dao)

        repository.save(userId = 1, request = "r", response = plan, sharedPlanId = "post-1")
        repository.delete(dao.rows.single())
        repository.save(userId = 1, request = "r", response = plan, sharedPlanId = "post-1")

        assertEquals(1, dao.rows.size)
    }

    @Test
    fun `a trip planned here is not a copy of anything`() = runBlocking {
        // Null must not collide: two locally planned trips are two trips, and a naive
        // "already have this sharedPlanId" check on null would keep only the first.
        val dao = FakeDao()
        val repository = SavedPlanRepository(dao)

        repository.save(userId = 1, request = "first", response = plan)
        repository.save(userId = 1, request = "second", response = plan)

        assertEquals(2, dao.rows.size)
        assertNull(dao.rows.first().sharedPlanId)
    }

    @Test
    fun `the copy remembers where it came from`() = runBlocking {
        val dao = FakeDao()
        val repository = SavedPlanRepository(dao)

        repository.save(userId = 1, request = "r", response = plan, sharedPlanId = "post-1")

        assertEquals("post-1", dao.rows.single().sharedPlanId)
    }
}
