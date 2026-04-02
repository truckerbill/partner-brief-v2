.PHONY: build send build-send

build:
	python3 "build_brief.py"

send:
	python3 "send_brief.py"

build-send: build send

