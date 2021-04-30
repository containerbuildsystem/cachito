# Using Cachito requests locally

The typical mode of interaction with the output of a Cachito request is through
[OSBS][osbs-cachito]. Installing the content from a finished request without the help of
OSBS may seem like a daunting task, but is in fact not that difficult. Doing so may be
especially useful when trying to debug a build failure without going through the entire
process again.

## Get the relevant files

A Cachito request has three main parts: the **archive**, the **environment variables**
and the **configuration files**. Getting these manually is a bit of a pain, which is
why we use the [cachito-download.sh](../bin/cachito-download.sh) script to do it. You
will need `jq` and the typical unix utils to run this script.

```shell
cachito-download.sh https://cachito.example.org/api/v1/requests/1 /tmp/cachito-1
```

## Use the right environment

If you are debugging a build, make sure to match the target environment as closely as
possible. For example, if you are building a container based on `python:3.9`, then you
may want to try out the build in that base image.

```shell
cd /tmp/cachito-1/remote-source
# mount the remote-source/ directory, later you will run the build from there
podman run --rm -ti -v "$PWD:$PWD:z" python:3.9 bash
```

If using a container is not an option/not relevant, you should still consider using some
kind of virtual environment (such as the Python venv) and at least making sure that the
versions of your build tools match what you will be using in the real build.

## Run the build

In the container/virtual environment/your own system (not recommended), set the
environment variables provided by Cachito and run the build.

```shell
cd /tmp/cachito-1/remote-source
# source the env vars from the generated file
source cachito.env
# cd to the app/ directory, some package managers will only work properly from there
cd app
# run your package manager commands
pip install -r requirements.txt
npm install
go build
# etc.
```

---

# Debugging failures

If you *have* resorted to trying out a build locally, it is probably because that build
is failing. The possible reasons are endless, but here you will find some of the most
useful concepts for debugging.

## Pip

Python's packaging system has its oddities, even more so when used through Cachito. The
[packaging glossary][packaging-glossary] may be useful to you throughout this section.

### Per project index

Your build uses a per project index, specified by the `PIP_INDEX_URL` environment
variable. This index contains only the source distributions (sdists) for your packages,
not wheels. That means all packages need to be built from source.

### Building from source

When building a package from source, the most important files are typically
**pyproject.toml** and, if the build backend is setuptools, **setup.cfg** or
**setup.py**. If you want to know more, check out [PEP 517][pep-517] and
[PEP 518][pep-518].

> :warning: PEPs 518 and 517 were implemented in pip versions 10.0 and 19.0,
> respectively. Additionally, only pip>=18.0 supports sdists for PEP 518. Older
> versions of pip will fail to install packages that rely on these PEPs.

If you just want to try installing packages from sdists, you do not need to go through
Cachito. You can simply pass the `--no-binary :all:` option to pip.

```shell
pip install --no-binary :all: -r requirements.txt
```

### Building extension modules

For pure Python modules, building from source is usually not a problem if you have all
the build dependencies. For extension modules written in C or other languages, this is
a bit trickier.

The build will still work if you have the build dependencies, but you cannot fetch them
through Cachito, not to mention that it is nearly impossible to determine what they are.
As always, `pip install --no-binary :all:` is your friend. Just keep trying until you
make it work.

[osbs-cachito]: https://osbs.readthedocs.io/en/latest/users.html#fetching-source-code-from-external-source-using-cachito
[packaging-glossary]: https://packaging.python.org/glossary/
[pep-517]: https://www.python.org/dev/peps/pep-0517/
[pep-518]: https://www.python.org/dev/peps/pep-0518/
