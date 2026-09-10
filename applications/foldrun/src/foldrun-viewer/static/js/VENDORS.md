# Vendored JavaScript Libraries

These libraries are provisioned via npm at Docker build time to eliminate
runtime CDN dependencies. The viewer runs in GCP VPCs where egress to
external CDNs may be restricted, so the JS files are served from the
container's static directory.

**The .min.js files are NOT committed to git.** They are installed from npm
during `docker build` and copied into the container image. See
`package.json` in the foldrun-viewer directory for version pins and the
`Dockerfile` for the build stage that provisions them.

| File | npm Package | Version | License |
|---|---|---|---|
| `3Dmol-min.js` | [3dmol](https://www.npmjs.com/package/3dmol) | ^2.5.5 | BSD-3-Clause |
| `marked.min.js` | [marked](https://www.npmjs.com/package/marked) | 11.1.1 | MIT |

## Updating

1. Edit `package.json` in the foldrun-viewer directory to update the version.
2. Rebuild the Docker image — the new version will be fetched automatically.
