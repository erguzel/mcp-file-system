from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from conftest import write
from mcp_file_system.context import AppContext
from mcp_file_system.core.models import Symbol
from mcp_file_system.core.outline.registry import default_registry, shebang_interpreter
from mcp_file_system.core.outline.treesitter import ShellOutlineProvider, YamlOutlineProvider
from mcp_file_system.core.outline.treesitter.structured import mask_templates
from mcp_file_system.errors import InvalidArgumentError


def table(symbols: list[Symbol]) -> list[tuple[str, int, int, int]]:
    return [(s.qualified_name, s.start_line, s.end_line, s.depth) for s in symbols]


SCRIPT = """\
#!/usr/bin/env bash
set -euo pipefail

install_k3s() {
  curl -sfL https://get.k3s.io | sh -
}

function wait_ready {
  kubectl wait --for=condition=Ready nodes --all
}

function main() {
  cleanup() { rm -rf "$tmp"; }
  trap cleanup EXIT
  install_k3s
  wait_ready
}

if [[ -n "${DEBUG:-}" ]]; then
  log() { echo "debug: $*"; }
else
  log() { :; }
fi

main "$@"
"""


class TestShell:
    def test_functions_in_both_syntaxes_nested_and_conditional(self) -> None:
        assert table(ShellOutlineProvider().outline(SCRIPT)) == [
            ("install_k3s", 4, 6, 0),
            ("wait_ready", 8, 10, 0),
            ("main", 12, 17, 0),
            ("cleanup", 13, 13, 1),
            ("log", 20, 20, 0),
            ("log", 22, 22, 0),
        ]

    def test_signature_and_kind(self) -> None:
        symbol = ShellOutlineProvider().outline(SCRIPT)[0]
        assert (symbol.kind, symbol.signature) == ("function", "install_k3s()")

    def test_conditional_redefinitions_are_read_together(self, ctx: AppContext, root: Path) -> None:
        write(root / "setup.sh", SCRIPT)
        result = ctx.outliner.read_symbol("setup.sh", "log")
        assert (result.start_line, result.end_line) == (20, 22)
        assert result.note == "function log (2 definitions)"

    def test_script_without_functions(self) -> None:
        assert ShellOutlineProvider().outline("echo hello\n") == []


class TestShebangDetection:
    @pytest.mark.parametrize(
        ("line", "interpreter"),
        [
            ("#!/bin/bash", "bash"),
            ("#!/bin/sh -e", "sh"),
            ("#! /usr/bin/env bash", "bash"),
            ("#!/usr/bin/env -S bash -x", "bash"),
            ("#!/usr/bin/env python3.12", "python"),
            ("#!/usr/local/bin/zsh", "zsh"),
            ("#!/usr/bin/env", None),
            ("# just a comment", None),
            ("", None),
        ],
    )
    def test_shebang_interpreter(self, line: str, interpreter: str | None) -> None:
        assert shebang_interpreter(line) == interpreter

    def test_extensionless_scripts_use_their_shebang(self, ctx: AppContext, root: Path) -> None:
        write(
            root / "bin" / "deploy", "#!/usr/bin/env bash\ndeploy() {\n  kubectl apply -f k8s/\n}\n"
        )
        write(root / "bin" / "tool", "#!/usr/bin/env python3\ndef main():\n    pass\n")
        assert ctx.outliner.outline("bin/deploy").language == "shell"
        assert ctx.outliner.read_symbol("bin/tool", "main").start_line == 2

    def test_unknown_file_lists_suffixes_and_interpreters(
        self, ctx: AppContext, root: Path
    ) -> None:
        write(root / "Makefile", "all:\n\techo hi\n")
        with pytest.raises(InvalidArgumentError, match=r"shebang interpreters: bash, .*python"):
            ctx.outliner.outline("Makefile")

    def test_registry_exposes_interpreters(self) -> None:
        interpreters = default_registry().supported_interpreters
        assert {"bash", "sh", "zsh", "python"} <= set(interpreters)


HELM = """\
{{/*
Deployment for the API.
*/}}
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ include "app.fullname" . }}
  labels:
    {{- include "app.labels" . | nindent 4 }}
spec:
  {{- if not .Values.autoscaling.enabled }}
  replicas: {{ .Values.replicaCount }}
  {{- end }}
  template:
    spec:
      containers:
        - name: {{ .Chart.Name }}
          image: "{{ .Values.image.repository }}:{{ .Values.image.tag }}"
          env:
            {{- range $key, $value := .Values.env }}
            - name: {{ $key }}
              value: {{ $value | quote }}
            {{- end }}
"""


class TestHelmTemplates:
    def test_templated_manifest_outlines_like_yaml(self) -> None:
        assert table(YamlOutlineProvider().outline(HELM)) == [
            ("apiVersion", 4, 4, 0),
            ("kind", 5, 5, 0),
            ("metadata", 6, 8, 0),
            ("metadata.name", 7, 7, 1),
            ("metadata.labels", 8, 8, 1),
            ("spec", 10, 23, 0),
            ("spec.replicas", 12, 12, 1),
            ("spec.template", 14, 23, 1),
            ("spec.template.spec", 15, 23, 2),
            ("spec.template.spec.containers", 16, 23, 3),
            ("spec.template.spec.containers[0]", 17, 23, 4),
            ("spec.template.spec.containers[0].name", 17, 17, 5),
            ("spec.template.spec.containers[0].image", 18, 18, 5),
            ("spec.template.spec.containers[0].env", 19, 23, 5),
            ("spec.template.spec.containers[0].env[0]", 21, 23, 6),
            ("spec.template.spec.containers[0].env[0].name", 21, 21, 7),
            ("spec.template.spec.containers[0].env[0].value", 22, 22, 7),
        ]

    def test_signatures_show_the_original_directives(self) -> None:
        symbols = {s.qualified_name: s for s in YamlOutlineProvider().outline(HELM)}
        assert symbols["spec.replicas"].signature == "replicas: {{ .Values.replicaCount }}"
        assert symbols["spec.template.spec.containers[0].image"].signature == (
            'image: "{{ .Values.image.repository }}:{{ .Values.image.tag }}"'
        )

    def test_templated_keys_keep_their_text(self) -> None:
        symbol = YamlOutlineProvider().outline("{{ .Values.key }}: 1\n")[0]
        assert symbol.name == "{{ .Values.key }}"

    def test_github_actions_expressions_are_unaffected(self) -> None:
        text = "env:\n  REF: ${{ github.ref }}\nrun: echo ${{ matrix.os }}\n"
        symbols = {s.qualified_name: s for s in YamlOutlineProvider().outline(text)}
        assert symbols["env.REF"].signature == "REF: ${{ github.ref }}"
        assert symbols["run"].signature == "run: echo ${{ matrix.os }}"

    def test_read_symbol_on_a_chart_template(self, ctx: AppContext, root: Path) -> None:
        write(root / "chart" / "templates" / "deployment.yaml", HELM)
        result = ctx.outliner.read_symbol("chart/templates/deployment.yaml", "spec.template")
        assert (result.start_line, result.end_line) == (14, 23)


class TestMaskTemplates:
    def test_plain_yaml_is_untouched(self) -> None:
        source = b"a: 1\nb: [x, y]\n"
        assert mask_templates(source) is source

    def test_inline_and_line_directives(self) -> None:
        source = b"a: {{ .x }}\n  {{- if .y }}\nb: 2\n"
        assert mask_templates(source) == b"a: xxxxxxxx\n              \nb: 2\n"

    def test_multiline_directive_keeps_newlines(self) -> None:
        source = b"{{/*\nnote\n*/}}\na: 1\n"
        masked = mask_templates(source)
        assert masked.count(b"\n") == source.count(b"\n")
        assert masked.endswith(b"a: 1\n")
        assert masked.strip(b" \n") == b"a: 1"

    @given(st.binary(max_size=200) | st.text(max_size=200).map(str.encode))
    def test_offsets_and_newlines_are_preserved(self, source: bytes) -> None:
        source = source.replace(b"!", b"{{").replace(b"?", b"}}")
        masked = mask_templates(source)
        assert len(masked) == len(source)
        assert [i for i, b in enumerate(masked) if b == 10] == [
            i for i, b in enumerate(source) if b == 10
        ]
