# Convenience wrapper around the scripts in this repo. Run from a directory
# containing exactly one .xapk (or set XAPK=path/to/file.xapk explicitly).
#
# Targets:
#   make deps      fetch_tools.py       -- external tools (skips what's already available)
#   make keystore  generate_keystore.sh -- ./sygic-patcher.jks
#   make extract   extract_skins.py     -- ./skin_override/ for skin edits
#   make patchall  build_patched_xapk.py --all (asks before overwriting an existing *_patched.xapk)
#   make all       deps + extract + patchall (keystore is a prerequisite of patchall)
#                  -- also the default: bare `make` == `make all`
#
# NOTE: XAPK auto-detection via $(wildcard) doesn't cope with filenames
# containing spaces (a GNU Make limitation, not this Makefile's) -- rename
# the file, or pass XAPK=... explicitly, if that's an issue.
#
# Pass V=1 (e.g. `make V=1` or `make deps V=1`) to see the underlying
# commands Make runs, instead of just this repo's own [+]/[i]/[-] output.

# *_patched.xapk is excluded since that's this Makefile's own output naming
# convention -- without this, a second `make` run would see its own prior
# output as a second candidate input and refuse to pick one.
XAPK := $(filter-out %_patched.xapk,$(wildcard *.xapk))
ifeq ($(strip $(XAPK)),)
$(error No .xapk file found in the current directory -- put one here, or run `make XAPK=path/to/file.xapk <target>`)
endif
ifneq ($(words $(XAPK)),1)
$(error Multiple .xapk files found ($(XAPK)) -- keep only one, or run `make XAPK=path/to/file.xapk <target>`)
endif

KEYSTORE := sygic-patcher.jks
SKIN_MARKER := skin_override/assets/res/skin/skin_config.xml
OUTPUT := $(basename $(XAPK))_patched.xapk

# Quiet by default -- pass V=1 (e.g. `make V=1` or `make deps V=1`) to see the
# underlying commands Make runs, prefixed with $(Q) below.
V ?= 0
Q = $(if $(filter 1,$(V)),,@)

.PHONY: deps keystore extract patchall all

# Bare `make` (no target) runs `make all`.
.DEFAULT_GOAL := all

deps:
	$(Q)python3 fetch_tools.py

keystore: $(KEYSTORE)

$(KEYSTORE):
	./generate_keystore.sh

extract: $(SKIN_MARKER)

$(SKIN_MARKER): $(XAPK)
	python3 extract_skins.py "$(XAPK)"

patchall: deps $(KEYSTORE)
	@if [ -e "$(OUTPUT)" ]; then \
		read -r -p "$(OUTPUT) already exists -- overwrite? [y/N] " reply </dev/tty; \
		case "$$reply" in \
			[yY]*) ;; \
			*) echo "[i] Skipped -- $(OUTPUT) left unchanged."; exit 0 ;; \
		esac; \
	fi; \
	python3 build_patched_xapk.py "$(XAPK)" "$(OUTPUT)" --all

all: deps extract patchall
