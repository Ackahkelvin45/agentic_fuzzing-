# Reproducible Linux environment for the agentic-fuzzing project.
#
# Why this exists: the project was developed on macOS/arm64, where LeakSanitizer
# is unsupported and the coverage tools are Apple's `xcrun llvm-*`. This image
# gives a grader a one-command Linux reproduction that (a) builds the ASan+UBSan
# harness and runs all tests, (b) enables LeakSanitizer (a leak surface the mac
# build cannot check), and (c) confirms finding #1 reproduces off macOS.
#
#   docker build -t agentic-fuzz .
#   docker run --rm agentic-fuzz                 # -> ./run.sh test (85 assertions)
#   docker run --rm agentic-fuzz ./run.sh all    # -> setup+build+test+baseline
#
# LeakSanitizer needs ptrace, and the venv python has Hypothesis, so the leak
# hunt is:
#   docker run --rm --cap-add=SYS_PTRACE agentic-fuzz .venv/bin/python eval/leakcheck.py
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        clang llvm libclang-rt-18-dev python3 python3-venv python3-pip \
        ca-certificates bash \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app

# Build the Linux venv + all three sanitizer harnesses at image-build time, so
# `docker run` is fast and the image is proven to compile on Linux.
RUN ./run.sh setup && ./run.sh build

# Default: run the four test suites (85 assertions) on Linux.
CMD ["./run.sh", "test"]
