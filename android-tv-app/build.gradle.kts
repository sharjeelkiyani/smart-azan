// Pinned (not "latest") versions on purpose: this project is built by CI
// months/years after being written, with no local machine to catch a
// version-skew break - a known-compatible fixed combo (see README.md in
// this folder) is safer than resolving to whatever's newest at build time.
plugins {
    id("com.android.application") version "8.5.2" apply false
    id("org.jetbrains.kotlin.android") version "1.9.24" apply false
}
