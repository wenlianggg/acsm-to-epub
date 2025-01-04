# acsm-to-epub

Downloads the file from the DRM content provider, and then removes the DRM from it using [libgourou](https://forge.soutade.fr/soutade/libgourou).

### Requirements

* Docker
* Docker Compose

### Usage Instructions

1. Put the .acsm files in the `input/` folder
2. Run `docker compose up --build`
3. Get the DRM-removed EPUB files in the `output/` folder

Note that once you convert the ACSM file, it will be tied to your anon credentials automatically created, and the ACSM file cannot be opened by any other device or program without the same set of credentials.

Fortunately, you have the EPUB files anyway!