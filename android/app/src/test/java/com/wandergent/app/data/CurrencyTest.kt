package com.wandergent.app.data

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull

class CurrencyTest {

    @Test
    fun `money shows a symbol, not a code`() {
        assertEquals("$120", formatMoney(120.0, "USD"))
        assertEquals("¥12000", formatMoney(12000.0, "JPY"))
        assertEquals("€200", formatMoney(200.0, "EUR"))
        assertEquals("฿15000", formatMoney(15000.0, "THB"))
    }

    @Test
    fun `an unknown code keeps the code rather than guessing a symbol`() {
        // Plans made before this setting existed, or a backend that chose the
        // destination's local currency. "1200 MXN" is honest; a wrong symbol is not.
        assertEquals("1200 MXN", formatMoney(1200.0, "MXN"))
        assertNull(Currency.fromCode("MXN"))
    }

    @Test
    fun `codes are matched case-insensitively`() {
        assertEquals(Currency.USD, Currency.fromCode("usd"))
        assertEquals(Currency.JPY, Currency.fromCode("Jpy"))
        assertNull(Currency.fromCode(null))
        assertNull(Currency.fromCode(""))
    }

    @Test
    fun `amounts are whole units`() {
        // Estimates, not invoices: decimals would imply a precision that is not there.
        assertEquals("$99", formatMoney(99.7, "USD"))
    }

    @Test
    fun `a new account settles up in US dollars`() {
        assertEquals(Currency.USD, Currency.DEFAULT)
    }

    @Test
    fun `the request carries the code, and omits it when unset`() {
        val json = ApiJson.json
        assertEquals(
            """{"message":"3 days in Los Angeles","currency":"USD"}""",
            json.encodeToString(PlanRequest(message = "3 days in Los Angeles", currency = "USD")),
        )
        // Empty means "you choose", which is what the backend did before the setting.
        assertEquals(
            """{"message":"3 days in Los Angeles"}""",
            json.encodeToString(PlanRequest(message = "3 days in Los Angeles")),
        )
    }
}
