#!/bin/bash
mkdir workdir
git clone git@github.com:release-engineering/retrodep workdir/sources

./venv/bin/python ./hack/process_gomod.py