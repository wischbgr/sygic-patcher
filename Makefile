# Convenience wrapper around the scripts in this repo. Run from a directory
# containing exactly one .xapk (or set XAPK=path/to/file.xapk explicitly).
#
# Targets:
#   make deps      fetch_tools.py       -- external tools (skips what's already available)
#   make keystore  generate_keystore.sh -- ./sygic-patcher.jks
#   make extract   extract_skins.py     -- ./skin_override/ for skin edits
#   make patchall  build_patched_xapk.py --all
#   make all       deps + extract + patchall (keystore is a prerequisite of patchall)
#
# NOTE: XAPK auto-detection via $(wildcard) doesn't cope with filenames
# containing spaces (a GNU Make limitation, not this Makefile's) -- rename
# the file, or pass XAPK=... explicitly, if that's an issue.

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

.PHONY: deps keystore extract patchall all

deps:
	python3 fetch_tools.py

keystore: $(KEYSTORE)

$(KEYSTORE):
	./generate_keystore.sh

extract: $(SKIN_MARKER)

$(SKIN_MARKER): $(XAPK)
	python3 extract_skins.py "$(XAPK)"

patchall: deps $(OUTPUT)

$(OUTPUT): $(XAPK) $(KEYSTORE)
	python3 build_patched_xapk.py "$(XAPK)" "$(OUTPUT)" --all

all: deps extract patchall
