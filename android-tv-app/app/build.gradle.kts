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
