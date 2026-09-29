package com.wandergent.app.data

/**
 * The currencies a traveller can settle up in, with the symbol to show instead of the code.
 *
 * The plan is *estimated* in the chosen currency, never converted into it -- a rate a few
 * hours old would turn an estimate into a number that only looks precise.
 */
enum class Currency(val code: String, val symbol: String, val label: String) {
    USD("USD", "$", "US Dollar"),
    EUR("EUR", "€", "Euro"),
    JPY("JPY", "¥", "Japanese Yen"),
    GBP("GBP", "£", "British Pound"),
    KRW("KRW", "₩", "Korean Won"),
    THB("THB", "฿", "Thai Baht"),
    SGD("SGD", "S$", "Singapore Dollar"),
    HKD("HKD", "HK$", "Hong Kong Dollar"),
    AUD("AUD", "A$", "Australian Dollar"),
    CHF("CHF", "CHF", "Swiss Franc");

    companion object {
        /** What a new account settles up in until it says otherwise. */
        val DEFAULT = USD

        fun fromCode(code: String?): Currency? =
            entries.firstOrNull { it.code.equals(code, ignoreCase = true) }
    }
}

/**
 * Money as a traveller reads it: symbol then amount, no decimals.
 *
 * An unlisted code keeps the code ("1200 MXN") rather than borrowing a symbol.
 */
fun formatMoney(amount: Double, code: String): String {
    val rounded = amount.toLong()
    return Currency.fromCode(code)?.let { "${it.symbol}$rounded" } ?: "$rounded $code"
}
