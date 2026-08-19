package com.wandergent.app.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * What the backend can supply for a caller who does not send every LLM header.
 *
 * The "AI model" form cannot label itself without this. Whether the model and endpoint
 * are required is a fact about the *server*: a deployment carrying no AI account of its
 * own needs both filled in, and one pointed at a provider it simply has no key for does
 * not. Guessing either way makes the form wrong -- it would demand typing nobody needs,
 * or accept a half-filled setting that only fails later, on the first plan.
 */
@Serializable
data class LlmSupport(
    val key: Boolean = false,
    val endpoint: Boolean = false,
    val model: Boolean = false,
) {
    companion object {
        /**
         * What to assume when `/health` could not be reached.
         *
         * Everything optional, which is what the app did before it asked at all. A failed
         * probe is not evidence that the server lacks an account, and inventing a
         * requirement from it would block someone whose backend is perfectly configured.
         * The server still refuses a request that is genuinely short a field, by name.
         */
        val UNKNOWN = LlmSupport(key = true, endpoint = true, model = true)
    }
}

@Serializable
data class HealthDto(
    val status: String = "",
    val app: String = "",
    @SerialName("llm") val llm: LlmSupport = LlmSupport.UNKNOWN,
)
