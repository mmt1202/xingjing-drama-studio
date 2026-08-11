import org.gradle.api.plugins.JavaPluginExtension

plugins {
    java
    alias(libs.plugins.spring.boot) apply false
    alias(libs.plugins.dependency.management) apply false
}

allprojects {
    group = "com.xingjing"
    version = "0.1.0-SNAPSHOT"
}

subprojects {
    apply(plugin = "java")

    extensions.configure<JavaPluginExtension> {
        toolchain {
            languageVersion = JavaLanguageVersion.of(21)
        }
    }

    tasks.withType<Test>().configureEach {
        useJUnitPlatform()
        testLogging {
            events("failed", "skipped")
        }
    }
}
