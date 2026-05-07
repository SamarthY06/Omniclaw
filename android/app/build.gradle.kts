import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// Read local.properties (gitignored) for API keys / signing info if present.
val localProps = Properties().apply {
    val f = rootProject.file("local.properties")
    if (f.exists()) f.inputStream().use { load(it) }
}

android {
    namespace = "com.ben"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.ben"
        minSdk = 30          // Android 11+. Lets us use foreground service mic constants.
        targetSdk = 35
        versionCode = 3
        versionName = "0.1.2"

        ndk {
            // nodejs-mobile ships these prebuilt; we match here so Gradle bundles
            // exactly the ABIs we have native deps for.
            abiFilters += listOf("arm64-v8a", "armeabi-v7a", "x86_64")
        }

        // Build our JNI shim (`libbennode.so`) which hosts the embedded Node engine.
        externalNativeBuild {
            cmake {
                cppFlags += "-std=c++17"
                arguments += listOf("-DANDROID_STL=c++_shared")
            }
        }

        // Embedded node payload baseline: AGENTS.md, TOOLS.md, etc., are copied
        // out of assets/node/workspace_bootstrap on first run.
        manifestPlaceholders["benVersionTag"] = versionName ?: "dev"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    externalNativeBuild {
        cmake {
            path = file("src/main/cpp/CMakeLists.txt")
            version = "3.22.1"
        }
    }

    buildTypes {
        debug {
            isDebuggable = true
            applicationIdSuffix = ""
            isMinifyEnabled = false
        }
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
        freeCompilerArgs += listOf("-Xjvm-default=all")
    }

    buildFeatures {
        viewBinding = true
    }

    packaging {
        resources {
            excludes += listOf(
                "META-INF/AL2.0",
                "META-INF/LGPL2.1",
                "META-INF/DEPENDENCIES"
            )
        }
        // nodejs-mobile ships its own libnode.so per ABI; do NOT pick first.
        jniLibs {
            useLegacyPackaging = true
        }
    }

    androidResources {
        // Don't compress the embedded node assets (already gzipped where it matters).
        noCompress += listOf("js", "json", "ts", "md", "node")
    }

    sourceSets["main"].assets.srcDirs("src/main/assets")
    // Bundle the prebuilt libnode.so binaries (one per ABI) that
    // android/scripts/fetch-nodejs-mobile.sh extracts into app/libnode/bin/.
    sourceSets["main"].jniLibs.srcDirs("libnode/bin")
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.activity:activity-ktx:1.9.3")
    implementation("androidx.fragment:fragment-ktx:1.8.4")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.6")
    implementation("androidx.lifecycle:lifecycle-service:2.8.6")
    implementation("androidx.lifecycle:lifecycle-viewmodel-ktx:2.8.6")
    implementation("androidx.recyclerview:recyclerview:1.3.2")
    implementation("androidx.constraintlayout:constraintlayout:2.2.0")
    implementation("androidx.swiperefreshlayout:swiperefreshlayout:1.1.0")
    implementation("androidx.preference:preference-ktx:1.2.1")
    // In-process broadcast for SettingsActivity -> HomeFragment phrase refresh
    implementation("androidx.localbroadcastmanager:localbroadcastmanager:1.1.0")
    implementation("com.google.android.material:material:1.12.0")
    implementation("androidx.security:security-crypto:1.1.0-alpha06")

    // ML Kit text recognition (on-device, ~bundled model)
    implementation("com.google.mlkit:text-recognition:16.0.1")

    // ZXing for QR scanning during pairing
    implementation("com.journeyapps:zxing-android-embedded:4.3.0")

    // WebRTC VAD via android-vad (used by SessionTimer to detect voice frames)
    implementation("com.github.gkonovalov.android-vad:webrtc:2.0.10")

    // OkHttp for OpenAI Realtime WSS
    implementation("com.squareup.okhttp3:okhttp:4.12.0")

    // Coroutines
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")

    // Embedded Node runtime: we ship libnode.so (per-ABI prebuilts) directly via
    // jniLibs.srcDirs("libnode/bin") and load it through our small JNI shim
    // built from src/main/cpp/CMakeLists.txt. There is intentionally no Gradle
    // dependency here - both come from app/libnode/, populated by
    // android/scripts/fetch-nodejs-mobile.sh.

    testImplementation("junit:junit:4.13.2")
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
    androidTestImplementation("androidx.test.espresso:espresso-core:3.6.1")
}
