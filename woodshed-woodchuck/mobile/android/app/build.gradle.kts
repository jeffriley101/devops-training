plugins { id("com.android.application") }

android {
    namespace = "com.woodshedwoodchuck.app"
    compileSdk = 36
    buildToolsVersion = "35.0.0"

    defaultConfig {
        applicationId = "com.woodshedwoodchuck.app"
        minSdk = 26
        targetSdk = 36
        versionCode = 1
        versionName = "0.1.0-r4b"
    }
    buildFeatures { buildConfig = true }
    buildTypes {
        release {
            // Deliberately unsigned. No signing secrets or release keys in R4B.
            isMinifyEnabled = false
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    lint { abortOnError = true; warningsAsErrors = true }
}

dependencies {
    testImplementation("junit:junit:4.13.2")
}
