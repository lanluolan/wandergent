package com.wandergent.app.data

/**
 * The currencies a traveller can settle up in, with the symbol to show instead of the
 * code.
 *
 * The plan is *estimated* in the chosen currency, never converted into it: a conversion
 * would need an exchange-rate source, and a rate a few hours old turns an estimate into
 * a number that looks precise and is not.
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
 * Falls back to the raw code for anything not in the list -- a plan generated before the
 * setting existed, or a backend that picked the destination's local currency. Showing
 * "1200 MXN" is honest; guessing a symbol is not.
 */
fun formatMoney(amount: Double, code: String): String {
    val rounded = amount.toLong()
    return Currency.fromCode(code)?.let { "${it.symbol}$rounded" } ?: "$rounded $code"
}
