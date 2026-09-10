# How to contribute

We'd love to accept your patches and contributions to this project.

## Before you begin

### Sign our Contributor License Agreement

Contributions to this project must be accompanied by a
[Contributor License Agreement](https://cla.developers.google.com/about) (CLA).
You (or your employer) retain the copyright to your contribution; this simply
gives us permission to use and redistribute your contributions as part of the
project.

If you or your current employer have already signed the Google CLA (even if it
was for a different project), you probably don't need to do it again.

Visit <https://cla.developers.google.com/> to see your current agreements or to
sign a new one.

### Review our community guidelines

This project follows
[Google's Open Source Community Guidelines](https://opensource.google/conduct/).

## Contribution process

### Code reviews

All submissions, including submissions by project members, require review. We
use GitHub pull requests for this purpose. Consult
[GitHub Help](https://help.github.com/articles/about-pull-requests/) for more
information on using pull requests.

### License headers

All source files must include a copyright license header. We use
[addlicense](https://github.com/google/addlicense) to manage headers.

Third-party code is not committed to this repository; it is provisioned at
build or install time. The node_modules/ directory (created during
provisioning) is excluded from license checks.

Before submitting a pull request, run addlicense to ensure all files have
headers:

    addlicense --ignore '**/node_modules/**' .

Or to check without modifying files:

    addlicense -check --ignore '**/node_modules/**' .

The tool uses Apache 2.0 license headers with "Google LLC" as the copyright
holder by default, matching this project's LICENSE file.

A pre-installed copy of addlicense is available at
`/scion-volumes/scratchpad/bin/addlicense` for agents operating within the
Scion environment.