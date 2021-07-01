# Cachito request metadata

* [Request JSON](#request-json)
* [Content Manifest](#content-manifest)
* [Request JSON vs. Content Manifest](#request-json-vs-content-manifest)

One of Cachito's secondary (but no less important) responsibilities is reporting all
the sources that were provided for a specific request. Cachito provides two different
metadata formats for this purpose.

The first format is the JSON response at `/api/v1/requests/{id}`, which contains all
the information about a request. In the rest of the document, this will be referred to
as [request JSON](#request-json).

The second format is the [Content Manifest](#content-manifest) at
`/api/v1/requests/{id}/content-manifest`. This is a JSON document as well, but contains
only information about the packages and dependencies present in the request bundle.

All the sources are also present in the request bundle downloaded from
`/api/v1/requests/{id}/download`. The packages from the requested git repository are
under `app/`. All the dependencies downloaded by Cachito are under `deps/`. If vendoring
is used, the dependencies will not be under `deps/` but instead somewhere under `app/`,
depending on the package manager. For example, vendored `gomod` dependencies will be
under `app/vendor/`, or `app/{subpath}/vendor` if the Go module is not in the repository
root. Vendoring is optional (enabled by a [flag](../README.md#flags)) and not all
package managers support it.

## Request JSON

The request JSON object has a lot of attributes. When it comes to reporting sources,
the relevant ones are `repo`, `ref`, `packages` and `dependencies`.

<details>
<summary>Example</summary>

```json
{
  "repo": "https://github.com/cachito-testing/cachito-yarn-lorem-ipsum",
  "ref": "b470410e50caff0447bc18ca4f011663681e7e17",
  "packages": [
    {
      "dependencies": [
        {
          "dev": true,
          "name": "yarn",
          "replaces": null,
          "type": "npm",
          "version": "1.22.10"
        }
      ],
      "name": "bootstrap-yarn",
      "path": "bootstrap-yarn",
      "type": "npm",
      "version": "1.0.0"
    },
    {
      "dependencies": [
        {
          "name": "axios",
          "replaces": null,
          "type": "yarn",
          "version": "0.21.1"
        },
        {
          "name": "follow-redirects",
          "replaces": null,
          "type": "yarn",
          "version": "1.14.0"
        }
      ],
      "name": "lorem-ipsum",
      "type": "yarn",
      "version": "1.0.0"
    }
  ],
  "dependencies": [
    {
      "dev": true,
      "name": "yarn",
      "replaces": null,
      "type": "npm",
      "version": "1.22.10"
    },
    {
      "name": "axios",
      "replaces": null,
      "type": "yarn",
      "version": "0.21.1"
    },
    {
      "name": "follow-redirects",
      "replaces": null,
      "type": "yarn",
      "version": "1.14.0"
    }
  ]
}
```
</details>

### repo, ref

The git repository and the exact commit reference that Cachito processed. In the example
above, that is [cachito-yarn-lorem-ipsum@bootstrap-yarn][cachito-yarn-lorem-ipsum].

The request bundle includes the entire repository, minus the `.git/` directory (unless
explicitly included by setting the `include-git-dir` flag). This makes the repository
one of the Cachito-provided sources.

### packages

The list of packages that Cachito was told to process. These always come from the
source repository. In the example above, the user specified that Cachito should process
the root of the repository as a `yarn` package and the `bootstrap-yarn` directory as an
`npm` package. Cachito then figured out the `name`, `version` and `dependencies` of both
of these packages using logic specific to the corresponding package managers.

```json
{
  "name": "bootstrap-yarn",
  "version": "1.0.0",
  "type": "npm",
  "path": "bootstrap-yarn",
  "dependencies": [
    {"name": "yarn", "version": "1.22.10", "type": "npm", "dev": true, "replaces": null}
  ]
}
```

^ the `bootstrap-yarn` package

#### package.name

The name of the package. Comes from the file that defines the package, such as
`package.json`, `go.mod`, `setup.cfg` etc.

#### package.version

The version of the package. Usually comes from the file that defines the package, such
as `package.json`, `setup.cfg` etc. Golang packages define their versions through git
data, see https://golang.org/doc/modules/version-numbers.

#### package.type

The type of the package. Corresponds to the name of the package manager that Cachito was
told to use when processing this package. The one exception is `go-package`, see
[go-package type][go-package-type] in the README.

#### package.path

The relative path to the package directory from the root of the repository. Only present
if the package is not in the root directory.

#### package.dependencies

The list of dependencies that Cachito downloaded for this package. See below.

### dependencies

The list of dependencies that Cachito downloaded. With some exceptions (see
[dependency.version](#dependencyversion)), these do not come from the source repository.
Dependencies appear both in `packages[].dependencies` and the top-level `dependencies`.
The former is a per-package listing of dependencies while the latter is a merged and
de-duplicated list of all the dependencies across all the packages.

```json
{
  "name": "yarn",
  "version": "1.22.10",
  "type": "npm",
  "dev": true,
  "replaces": null
}
```

In this somewhat funny example, Cachito downloaded `yarn` as a dev dependency of an
`npm` package ([bootstrap-yarn](#packages)).

#### dependency.name

The name of the dependency. Comes from the file that defines what dependencies the
package needs, such as `package-lock.json`, `go.mod`, `requirements.txt` etc.

#### dependency.version

The version of the dependency. Like the name, this comes from the file that defines the
dependencies.

Unlike the package version, which is normally a valid semantic version according to the
package manager, the dependency versions have a much wider range of possible values.
This is because most package managers let you specify dependencies not only with the
name and version, but also support git repos, http(s) tarballs, local files etc. See
[External Dependencies][feature-support] in the README.

<details>
<summary>Example - NPM dependency from a URL</summary>

```json
{
  "name": "fecha",
  "version": "https://github.com/taylorhakes/fecha/archive/91680e4db1415fea33eac878cfd889c80a7b55c7.tar.gz",
  "type": "npm",
  "dev": false,
  "replaces": null
}
```
</details>

While the version value may not always be a valid semantic version, it *should* always
be a valid way to specify the dependency within the rules of the given package manager.

Now comes the time to explain why some dependencies may break the unwritten rule of
never coming from the source repository. Package managers that support local files as
dependencies can, in fact, use dependencies from the source repository. However, they
can only do so if Cachito allows it. See [Nested Dependencies][feature-support] in the
README.

<details>
<summary>Example - Gomod local dependency</summary>

```json
{
  "name": "k8s.io/kubectl",
  "version": "./staging/src/k8s.io/kubectl",
  "type": "gomod",
  "replaces": null
}
```
</details>

#### dependency.type

The type of the dependency. This always matches the type of the package that uses this
dependency.

#### dependency.dev

True if this is a dev dependency, false otherwise. The README explains the general idea
behind dev dependencies in the [Dev Dependencies][feature-support] section. The exact
semantics differ slightly for each package manager.

Note that this key is only present if Cachito supports identifying dev dependencies for
the given package type. Golang is, once again, an exception where the `dev` key will not
be present even though the README claims it is supported. Cachito does not support
Golang dev dependencies per se. However, `gomod` packages can, in a way, be treated as
dev. See the README section about the [go-package type][go-package-type].

#### dependency.replaces

When creating a Cachito request, the user can specify dependencies that Cachito should
replace (see [Dependency Replacements][feature-support]). If the `replaces` value is not
`null`, then it is an object that describes the original dependency that would have been
used if Cachito had not replaced it.

<details>
<summary>Example - semver@v1.4.0 replaces semver@v1.4.2</summary>

```json
{
  "name": "github.com/Masterminds/semver",
  "version": "v1.4.0",
  "type": "gomod",
  "replaces": {
    "name": "github.com/Masterminds/semver",
    "version": "v1.4.2",
    "type": "gomod"
  }
}
```
</details>

Note that this does not apply to replacement mechanisms supported natively by some
package managers, such as the go.mod [replace][gomod-replace] directive. Those
replacements will not be reflected in the request JSON in any way.

## Content Manifest

The Content Manifest (full name Image Content Manifest) is a document that describes the
content present in a container image. A JSON Schema specification is available in the
[containerbuildystem/atomic-reactor][atomic-reactor] repository (part of [OSBS]):
[content_manifest.json].

Cachito itself knows nothing about container images. The document returned from the
`/api/v1/requests/{id}/content-manifest` endpoint conforms to the schema but contains
placeholder values for some metadata attributes. The only part of the Content Manifest
that Cachito *does* pre-fill with relevant data is the `image_contents` attribute.

<details>
<summary>Example</summary>

Content Manifest for [cachito-yarn-lorem-ipsum@bootstrap-yarn][cachito-yarn-lorem-ipsum]

```json
{
  "image_contents": [
    {
      "dependencies": [
        {
          "purl": "pkg:npm/axios@0.21.1"
        },
        {
          "purl": "pkg:npm/follow-redirects@1.14.0"
        }
      ],
      "purl": "pkg:github/cachito-testing/cachito-yarn-lorem-ipsum@b470410e50caff0447bc18ca4f011663681e7e17",
      "sources": [
        {
          "purl": "pkg:npm/axios@0.21.1"
        },
        {
          "purl": "pkg:npm/follow-redirects@1.14.0"
        }
      ]
    },
    {
      "dependencies": [],
      "purl": "pkg:github/cachito-testing/cachito-yarn-lorem-ipsum@b470410e50caff0447bc18ca4f011663681e7e17#bootstrap-yarn",
      "sources": [
        {
          "purl": "pkg:npm/yarn@1.22.10"
        }
      ]
    }
  ],
  "metadata": {
    "icm_spec": "https://raw.githubusercontent.com/containerbuildsystem/atomic-reactor/f4abcfdaf8247a6b074f94fa84f3846f82d781c6/atomic_reactor/schemas/content_manifest.json",
    "icm_version": 1,
    "image_layer_index": -1
  }
}
```
</details>

### image_contents

The list of all the packages that Cachito provided to the container image build. It does
not necessarily cover all the content that will be present in the final image. Build
systems that integrate with Cachito may wish to extend this list with additional data
of their own.

Each package in this list is an object with three attributes: `purl`, `dependencies`
and `sources`.

### purl

Identifies a package via a [purl][purl-spec] (a package URL). It is important to note
that the primary purpose of a purl is to *locate* a package on the internet. Let's
compare the packages from the [request JSON](#request-json) example to their purl
representations.

```json
[
  {"name": "lorem-ipsum", "version": "1.0.0", "type": "yarn"},
  {"name": "bootstrap-yarn", "version": "1.0.0", "type": "npm", "path": "bootstrap-yarn"}
]
```
```
pkg:github/cachito-testing/cachito-yarn-lorem-ipsum@b470410e50caff0447bc18ca4f011663681e7e17
pkg:github/cachito-testing/cachito-yarn-lorem-ipsum@b470410e50caff0447bc18ca4f011663681e7e17#bootstrap-yarn
```

The JSON data shows the name, version and type of the package (more on that in the
[packages](#packages) section). Normally, this would be enough to locate the package.
Just get `lorem-ipsum@1.0.0` from https://registry.yarnpkg.com/, right? However, Cachito
does not know if lorem-ipsum is available in the yarn registry. Nor does it know if the
lorem-ipsum in the registry is the same lorem-ipsum as your git repository. Or if the
1.0.0 version from the registry is still the same as your 1.0.0 version. From Cachito's
point of view, the only viable way to identify the package is to point to the exact
commit in your git repository.

It is impossible to convert between Cachito's JSON and purl representations of packages
without context. To convert to purl, you need to know the [repo+ref](#repo-ref) of the
request and the [path](#packagepath) of the package. To convert from purl to JSON, you
would need to inspect the repository at the correct commit ref and extract the name and
version of the package(s) present there. It may not be possible to do so unambiguously.
If there are multiple packages present in the same directory, Cachito's purl will not
give you anything to distinguish between them. If they are in different directories,
they can be identified by the subpath fragment in the purl.

Most of this does not apply to [golang purls](#golang-packages).

### dependencies

The list of all [non-dev](#dependencydev) dependencies for this package. For interpreted
languages, this is the list of runtime dependencies. For compiled languages, this is
typically the list of everything needed for compilation. For Golang specifically, this
is the list of all the [go-package][go-package-type] dependencies.

Each dependency in the list is an object with a single attribute: `purl`.

```json
[
  {"purl": "pkg:npm/axios@0.21.1"},
  {"purl": "pkg:npm/follow-redirects@1.14.0"}
]
```

Like the package purl, the purl of each dependency is intended to locate that dependency
on the internet. Unlike packages, which are part of the git repository that Cachito
processed, dependencies usually come from official package repositories such as PyPI or
the NPM/Yarn registry. This allows Cachito to use more specialized purl types which
convey more information about the name and version of the dependencies.

Often, the JSON and purl representations of dependencies carry equivalent information,
and it is possible to convert between them directly. Sometimes, type information can be
lost, for example `npm` and `yarn` dependencies are both represented as `pkg:npm`. That
is usually not important, in the NPM/Yarn case, the two are just different clients for
the same package repository.

Dependencies that do not come from official sources typically have to use more generic
purl types, see [external dependencies](#external-dependencies).

### sources

For most package types, this is the list of all dependencies, i.e., a union of dev and
non-dev dependencies. For Golang, this is the list of all the [gomod][go-package-type]
dependencies.

Much like the `dependencies` list, each dependency listed here is an object with
a single `purl` attribute. The purls likewise follow the same logic as those in
`dependencies`.

### Special cases

#### Golang packages

Unlike most languages, Golang has no centralized package repository. In the Golang
ecosystem, the name of a package must reflect its location on the internet. This allows
Cachito to use the specialized `pkg:golang` purls even for packages, whereas for other
languages Cachito can only afford to do so for dependencies.

<details>
<summary>Example</summary>

```json
{"name": "github.com/release-engineering/retrodep/v2", "version": "v2.1.1", "type": "go-package"}
```
```
pkg:golang/github.com%2Frelease-engineering%2Fretrodep%2Fv2@v2.1.1
```
</details>

One unfortunate downside of this approach is that if your repository is a fork intended
for use with the [replace][gomod-replace] directive, the purl will be incorrect. Take
for example the [openshift/kubernetes-apiserver][openshift-apiserver] repository. The
name in `go.mod` is `k8s.io/apiserver`, which is the name of the package that this fork
is meant to replace. That is expected as per the `replace` rules, but it also causes the
purl to incorrectly reference the original rather than the fork.

#### External dependencies

Many package managers let you use dependencies from non-standard sources, such as git
repos (this is standard for Golang of course), http(s) tarballs etc. - see
[External Dependencies][feature-support] in the README. Cachito typically has to use
generic purls pointing to the repository or bare url where the dependency came from.

<details>
<summary>Example - NPM dependency from a URL</summary>

```json
{
  "name": "fecha",
  "version": "https://github.com/taylorhakes/fecha/archive/91680e4db1415fea33eac878cfd889c80a7b55c7.tar.gz",
  "type": "npm"
}
```
```
pkg:generic/fecha?download_url=https%3A%2F%2Fgithub.com%2Ftaylorhakes%2Ffecha%2Farchive%2F91680e4db1415fea33eac878cfd889c80a7b55c7.tar.gz
```
</details>

<details>
<summary>Example - pip dependency from a git repo</summary>

```json
{
  "name": "dockerfile-parse",
  "version": "git+https://github.com/containerbuildsystem/dockerfile-parse@ed38dc2eadf6890b524340f45a7d172d02196e61",
  "type": "pip"
}
```
```
pkg:github/containerbuildsystem/dockerfile-parse@ed38dc2eadf6890b524340f45a7d172d02196e61
```
</details>

#### Local dependencies

Often, package managers also support using local files as dependencies, see
[Nested Dependencies][feature-support] in the README. For local dependencies, Cachito
constructs the purl by taking the purl of the package that uses that dependency and
appending the relative path from the package to the dependency to the package purl.

<details>
<summary>Example</summary>

The package

```json
{
  "name": "k8s.io/kubernetes",
  "version": "v1.22.0",
  "type": "gomod"
}
```
```
pkg:golang/k8s.io%2Fkubernetes@v1.22.0
```

The local dependency

```json
{
  "name": "k8s.io/kubectl",
  "version": "./staging/src/k8s.io/kubectl",
  "type": "gomod"
}
```
```
pkg:golang/k8s.io%2Fkubernetes@v1.22.0#staging/src/k8s.io/kubectl
```
</details>

In the Golang world with its modules and packages this is, as usual, more complicated.
The example above pretends that `k8s.io/kubernetes` is a package and uses
`k8s.io/client-go` as a dependency. That is not true - `k8s.io/kubernetes` is a *module*
and contains many *packages*, but is not a package itself (you can tell by the absence
of `*.go` files in the root of the [repo](https://github.com/kubernetes/kubernetes)).
A more realistic example would be to use `k8s.io/kubernetes/cmd/kubeadm` as the package.
The purls would look as follows:

```
# k8s.io/kubernetes/cmd/kubeadm
pkg:golang/k8s.io%2Fkubernetes%2Fcmd%2Fkubeadm@v1.22.0
# k8s.io/client-go
pkg:golang/k8s.io%2Fkubernetes@v1.22.0#staging/src/k8s.io/kubectl
```

But wait, why is the purl for `k8s.io/client-go` relative to `k8s.io/kubernetes`, not
to `k8s.io/kubernetes/cmd/kubeadm`? Dependency management is fully up to the module.
All the dependencies, including local ones, are defined in the go.mod file in the
module. The paths are likewise relative to the module, not to the package that ends up
using the dependencies. That is why purls for local dependencies are relative to modules
and not packages.

## Request JSON vs. Content Manifest

Both formats attempt to represent similar information about a Cachito request. Both do
an okay-ish job. The Content Manifest does not contain any extra information compared to
the request JSON, though the purl format should, in theory, be easier to use for the
purpose of locating packages and dependencies. The Content Manifest does lack some
information present in the request JSON though.

### Missing in Content Manifest

#### Repo and ref

The Content Manifest does not state which repository and which commit in that
repository Cachito processed. The information *may* be present in the form of package
purls (see [purl](#purl)), but that is coincidental rather than intentional. Requests
with no packages will not include this info at all.

#### Package metadata

The request JSON includes the names, versions and types of packages found in the
processed repository (see [packages](#packages)). For non-golang packages, the Content
Manifest does not - unless you are willing to inspect the repository and re-process
the metadata.

#### Dependency replacements

See [dependency.replaces](#dependencyreplaces). The request JSON lists both the
replacement and the original. The Content Manifest lists only the replacement, not
the original.

#### Golang modules from the repository

Any Golang repository processed by Cachito will contain both modules and packages (see
[go-package type][go-package-type]). The Content Manifest lists only the packages. That
is usually good enough. Modules for dependencies *are* listed in [sources](#sources).

[cachito-yarn-lorem-ipsum]: https://github.com/cachito-testing/cachito-yarn-lorem-ipsum/tree/b470410e50caff0447bc18ca4f011663681e7e17
[go-package-type]: ../README.md#go-package-level-dependencies-and-the-go-package-cachito-package-type
[feature-support]: ../README.md#feature-support
[gomod-replace]: https://golang.org/ref/mod#go-mod-file-replace
[atomic-reactor]: https://github.com/containerbuildsystem/atomic-reactor
[content_manifest.json]: https://github.com/containerbuildsystem/atomic-reactor/blob/master/atomic_reactor/schemas/content_manifest.json
[OSBS]: https://osbs.readthedocs.io
[purl-spec]: https://github.com/package-url/purl-spec
[openshift-apiserver]: https://github.com/openshift/kubernetes-apiserver
