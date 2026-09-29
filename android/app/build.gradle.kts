plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.compose.compiler)
    alias(libs.plugins.kotlin.serialization)
    alias(libs.plugins.ksp)
}

android {
    namespace = "com.wandergent.app"
    compileSdk = 37

    defaultConfig {
        applicationId = "com.wandergent.app"
        minSdk = 26
        targetSdk = 37
        versionCode = 3
        versionName = "0.1.2"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"

        // The default is reached over `adb reverse tcp:8000 tcp:8000`, which works for a
        // USB device and an emulator alike and needs no shared Wi-Fi, host IP lookup or
        // inbound firewall hole -- so the backend can stay bound to loopback. A cable-less
        // phone or a deployed backend is a rebuild rather than a setting: shipping an
        // address field in a travel app advertises a dev build to every user. Override
        // with `-Pwandergent.serverUrl=192.168.1.10:8000`.
        val serverUrl = (findProperty("wandergent.serverUrl") as String?)
            ?.trim()
            ?.takeIf { it.isNotEmpty() }
            ?: "127.0.0.1:8000"
        // Goes into generated Kotlin source as a string literal, so validate as a
        // whitelist rather than trying to escape whatever arrives on the command line.
        require(serverUrl.matches(Regex("[A-Za-z0-9._:/-]+"))) {
            "wandergent.serverUrl is not a plausible address: $serverUrl"
        }
        buildConfigField("String", "BASE_URL", "\"$serverUrl\"")
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

dependencies {
    val composeBom = platform(libs.compose.bom)
    implementation(composeBom)

    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.lifecycle.runtime.compose)

    implementation(libs.androidx.navigation.compose)
    implementation(libs.compose.material3)
    implementation(libs.compose.material.icons)
    implementation(libs.compose.ui)

    implementation(libs.retrofit)
    implementation(libs.retrofit.serialization)
    implementation(libs.okhttp.logging)
    implementation(libs.okhttp.sse)
    implementation(libs.kotlinx.serialization.json)

    implementation(libs.androidx.datastore.preferences)
    implementation(libs.androidx.room.runtime)
    implementation(libs.androidx.room.ktx)
    ksp(libs.androidx.room.compiler)

    testImplementation(libs.junit)
    testImplementation(libs.kotlin.test.junit)
    androidTestImplementation(composeBom)
    androidTestImplementation("androidx.compose.ui:ui-test-junit4")
    debugImplementation("androidx.compose.ui:ui-test-manifest")
}
