plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

android {
    namespace = "com.brainbot.indierecorder"
    compileSdk = 35
    buildToolsVersion = "35.0.0"

    defaultConfig {
        applicationId = "com.brainbot.indierecorder"
        minSdk = 26
        targetSdk = 35
        versionCode = 9
        versionName = "0.5.3"
        // Where a typed connect code is looked up, when a build names it (CONNECT_BASE: a recorder
        // behind an address with a public certificate, android/build.sh takes it from the box's
        // .env). Without one, the build on GitHub for any box, the connect screen asks for the
        // box's address.
        val connectBase = System.getenv("CONNECT_BASE") ?: ""
        buildConfigField("String", "CONNECT_BASE", "\"" + connectBase.trimEnd('/') + "\"")
    }

    // Release builds are signed with the key in RELEASE_KEYSTORE, its password
    // in the file RELEASE_KEYSTORE_PASSWORD_FILE (both kept on the box, never in
    // the repo); without them they fall back to the debug key.
    signingConfigs {
        create("release") {
            val path = System.getenv("RELEASE_KEYSTORE")
            if (path != null) {
                val password = file(System.getenv("RELEASE_KEYSTORE_PASSWORD_FILE")).readText().trim()
                storeFile = file(path)
                storePassword = password
                keyAlias = System.getenv("RELEASE_KEY_ALIAS") ?: "indie-recorder"
                keyPassword = password
            }
        }
    }
    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = if (System.getenv("RELEASE_KEYSTORE") != null) signingConfigs.getByName("release")
                            else signingConfigs.getByName("debug")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    buildFeatures {
        compose = true
        buildConfig = true
    }
    packaging {
        resources.excludes += "/META-INF/{AL2.0,LGPL2.1}"
    }
}

dependencies {
    implementation(platform("androidx.compose:compose-bom:2024.12.01"))
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.activity:activity-compose:1.9.3")
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.7")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.8.7")
    implementation("androidx.work:work-runtime-ktx:2.10.0")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.9.0")
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
}
