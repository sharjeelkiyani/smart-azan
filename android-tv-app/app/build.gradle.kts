plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.smartazan.tvdisplay"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.smartazan.tvdisplay"
        // minSdk 21 covers Fire TV Stick (1st gen, 2016+) through today's
        // devices - this app is just a WebView, nothing needing a newer API.
        minSdk = 21
        targetSdk = 33
        versionCode = 1
        versionName = "1.0"
    }

    // Sign every build with the same key, so an installed app can always be
    // updated in place. CI decodes the ANDROID_DEBUG_KEYSTORE_B64 repo secret to
    // the path in SMART_AZAN_KEYSTORE; without it (a local build) the normal
    // per-machine debug key is used. Relying on ~/.android/debug.keystore alone
    // didn't work on CI: every build came out with a different signature.
    signingConfigs {
        getByName("debug") {
            System.getenv("SMART_AZAN_KEYSTORE")?.let { path ->
                storeFile = file(path)
                storePassword = "android"
                keyAlias = "androiddebugkey"
                keyPassword = "android"
            }
        }
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

    kotlinOptions {
        jvmTarget = "17"
    }
}
