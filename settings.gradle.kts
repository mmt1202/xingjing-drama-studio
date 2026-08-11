import org.gradle.api.initialization.resolve.RepositoriesMode

rootProject.name = "xingjing-platform"

dependencyResolutionManagement {
    repositoriesMode = RepositoriesMode.FAIL_ON_PROJECT_REPOS
    repositories {
        mavenCentral()
    }
}

include("platform-services:platform-core")
include("platform-services:platform-app")
