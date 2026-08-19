import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

val releaseSigningPropertiesFile =
    File(System.getProperty("user.home"), ".android/khjw-signing/signing.properties")
val releaseSigningProperties = Properties().apply {
    if (releaseSigningPropertiesFile.isFile) {
        releaseSigningPropertiesFile.inputStream().use(::load)
    }
}

val requiredReleaseSigningProperties =
    listOf("storeFile", "storePassword", "keyAlias", "keyPassword")

val validateReleaseSigning by tasks.registering {
    group = "verification"
    description = "Validates the external KHJW release signing configuration."

    doLast {
        if (!releaseSigningPropertiesFile.isFile) {
            throw GradleException(
                "KHJW release signing file is required at " +
                    "~/.android/khjw-signing/signing.properties"
            )
        }

        val missingProperties = requiredReleaseSigningProperties.filter {
            releaseSigningProperties.getProperty(it).isNullOrBlank()
        }
        if (missingProperties.isNotEmpty()) {
            throw GradleException(
                "KHJW release signing file is missing required properties: " +
                    missingProperties.joinToString()
            )
        }

        val configuredStoreFile = File(releaseSigningProperties.getProperty("storeFile"))
        if (!configuredStoreFile.isFile) {
            throw GradleException("The KHJW release keystore configured by signing.properties is unavailable.")
        }
    }
}

android {
    namespace = "com.hoojshwah.khjw"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.hoojshwah.khjw"
        minSdk = 26
        targetSdk = 36
        versionCode = 1
        versionName = "1.0.0-beta1"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    signingConfigs {
        create("release") {
            if (releaseSigningPropertiesFile.isFile) {
                releaseSigningProperties.getProperty("storeFile")
                    ?.takeIf(String::isNotBlank)
                    ?.let { storeFile = File(it) }
                storePassword = releaseSigningProperties.getProperty("storePassword")
                keyAlias = releaseSigningProperties.getProperty("keyAlias")
                keyPassword = releaseSigningProperties.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            signingConfig = signingConfigs.getByName("release")
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
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

tasks.matching { it.name == "preReleaseBuild" }.configureEach {
    dependsOn(validateReleaseSigning)
}

dependencies {
    implementation("androidx.core:core-ktx:1.18.0")
    implementation("androidx.appcompat:appcompat:1.8.0")
    implementation("androidx.activity:activity-ktx:1.13.0")
    implementation("androidx.webkit:webkit:1.16.0")
    implementation("androidx.media3:media3-exoplayer:1.11.0")
    implementation("androidx.media3:media3-session:1.11.0")

    testImplementation("junit:junit:4.13.2")
}
