import contextlib
import os
import re
import shutil
import warnings
from datetime import datetime
from distutils.util import strtobool
from importlib_metadata import metadata as extract_metadata
from os import path
from unittest import mock
from urllib.parse import urljoin

from sphinx_gallery.sorting import ExampleTitleSortKey, ExplicitOrder
from tqdm import tqdm

import torch

from pystiche.misc import download_file

HERE = path.dirname(__file__)
PROJECT_ROOT = path.abspath(path.join(HERE, "..", ".."))


def get_bool_env_var(name, default=False):
    try:
        return bool(strtobool(os.environ[name]))
    except KeyError:
        return default


GITHUB_ACTIONS = get_bool_env_var("GITHUB_ACTIONS")
RTD = get_bool_env_var("READTHEDOCS")
CI = GITHUB_ACTIONS or RTD or get_bool_env_var("CI")


def project():
    extension = None

    metadata = extract_metadata("pystiche")
    project = metadata["name"]
    author = metadata["author"]
    copyright = f"{datetime.now().year}, {author}"
    release = metadata["version"]
    version = release.split(".dev")[0]
    config = dict(
        project=project,
        author=author,
        copyright=copyright,
        release=release,
        version=version,
    )

    return extension, config


def autodoc():
    extensions = [
        "sphinx.ext.autodoc",
        "sphinx.ext.napoleon",
        "sphinx_autodoc_typehints",
    ]

    config = None

    return extensions, config


def intersphinx():
    extension = "sphinx.ext.intersphinx"
    config = dict(
        intersphinx_mapping={
            "python": ("https://docs.python.org/3.6", None),
            "torch": ("https://pytorch.org/docs/stable/", None),
            "torchvision": ("https://pytorch.org/docs/stable/", None),
            "PIL": ("https://pillow.readthedocs.io/en/stable/", None),
            "numpy": ("https://numpy.org/doc/1.18/", None),
            "requests": ("https://requests.readthedocs.io/en/stable/", None),
            "matplotlib": ("https://matplotlib.org", None),
        }
    )
    return extension, config


def html():
    extension = None

    config = dict(
        html_theme="pydata_sphinx_theme",
        html_theme_options=dict(show_prev_next=False, use_edit_page_button=True),
        html_context=dict(
            github_user="pystiche",
            github_repo="pystiche",
            github_version="main",
            doc_path="docs/source",
        ),
    )

    return extension, config


def latex():
    extension = None

    with open(path.join(HERE, "custom_cmds.tex"), "r") as fh:
        custom_cmds = fh.read()
    config = dict(
        latex_elements={"preamble": custom_cmds},
        mathjax_inline=[r"\(" + custom_cmds, r"\)"],
        mathjax_display=[r"\[" + custom_cmds, r"\]"],
    )

    return extension, config


def bibtex():
    extension = "sphinxcontrib.bibtex"

    config = dict(bibtex_bibfiles=["references.bib"])

    return extension, config


def doctest():
    extension = "sphinx.ext.doctest"

    doctest_global_setup = """
import torch
from torch import nn

import pystiche

import warnings
warnings.filterwarnings("ignore", category=FutureWarning)

from unittest import mock

patcher = mock.patch(
    "pystiche.enc.models.utils.ModelMultiLayerEncoder.load_state_dict_from_url"
)
patcher.start()
"""

    doctest_global_cleanup = """
mock.patch.stopall()
"""
    config = dict(
        doctest_global_setup=doctest_global_setup,
        doctest_global_cleanup=doctest_global_cleanup,
    )

    return extension, config


def sphinx_gallery():
    extension = "sphinx_gallery.gen_gallery"

    plot_gallery = get_bool_env_var("PYSTICHE_PLOT_GALLERY", default=not CI)
    download_gallery = get_bool_env_var("PYSTICHE_DOWNLOAD_GALLERY", default=CI)

    def download():
        nonlocal extension
        nonlocal plot_gallery

        # version and release are available as soon as the project config is loaded
        version = globals()["version"]
        release = globals()["release"]

        base = "https://download.pystiche.org/galleries/"
        is_dev = version != release
        file = "main.zip" if is_dev else f"v{version}.zip"

        url = urljoin(base, file)
        print(f"Downloading pre-built galleries from {url}")
        download_file(url, file)

        with contextlib.suppress(FileNotFoundError):
            shutil.rmtree(path.join(HERE, "galleries"))
        shutil.unpack_archive(file, extract_dir=".")
        os.remove(file)

        extension = "sphinx_gallery.load_style"
        plot_gallery = False

    def show_cuda_memory(func):
        torch.cuda.reset_peak_memory_stats()
        out = func()

        stats = torch.cuda.memory_stats()
        peak_bytes_usage = stats["allocated_bytes.all.peak"]
        memory = peak_bytes_usage / 1024 ** 2

        return memory, out

    def patch_tqdm():
        patchers = [mock.patch("tqdm.std._supports_unicode", return_value=True)]

        display = tqdm.display
        close = tqdm.close
        displayed = set()

        def display_only_last(self, msg=None, pos=None):
            if self.n != self.total or self in displayed:
                return

            display(self, msg=msg, pos=pos)
            displayed.add(self)

        patchers.append(mock.patch("tqdm.std.tqdm.display", new=display_only_last))

        def close_(self):
            close(self)
            with contextlib.suppress(KeyError):
                displayed.remove(self)

        patchers.append(mock.patch("tqdm.std.tqdm.close", new=close_))

        for patcher in patchers:
            patcher.start()

    class PysticheExampleTitleSortKey(ExampleTitleSortKey):
        def __call__(self, filename):
            # The beginner example *without* pystiche is placed before the example
            # *with* to clarify the narrative.
            if filename == "example_nst_without_pystiche.py":
                return "1"
            elif filename == "example_nst_with_pystiche.py":
                return "2"
            else:
                return super().__call__(filename)

    def filter_warnings():
        # See #https://github.com/pytorch/pytorch/issues/60053
        warnings.filterwarnings(
            "ignore",
            category=UserWarning,
            message=(
                re.escape(
                    "Named tensors and all their associated APIs are an experimental "
                    "feature and subject to change. Please do not use them for "
                    "anything important until they are released as stable. (Triggered "
                    "internally at  /pytorch/c10/core/TensorImpl.h:1156.)"
                )
            ),
        )

    if download_gallery:
        download()

    if plot_gallery and not torch.cuda.is_available():
        msg = (
            "The galleries will be built, but CUDA is not available. "
            "This will take a long time."
        )
        print(msg)

    sphinx_gallery_conf = {
        "examples_dirs": path.join(PROJECT_ROOT, "examples"),
        "gallery_dirs": path.join("galleries", "examples"),
        "filename_pattern": re.escape(os.sep) + r"example_\w+[.]py$",
        "ignore_pattern": re.escape(os.sep) + r"_\w+[.]py$",
        "line_numbers": True,
        "remove_config_comments": True,
        "plot_gallery": plot_gallery,
        "subsection_order": ExplicitOrder(
            [
                path.join("..", "..", "examples", sub_gallery)
                for sub_gallery in ("beginner", "advanced")
            ]
        ),
        "within_subsection_order": PysticheExampleTitleSortKey,
        "show_memory": show_cuda_memory if torch.cuda.is_available() else True,
    }

    config = dict(sphinx_gallery_conf=sphinx_gallery_conf)
    filter_warnings()

    patch_tqdm()
    filter_warnings()

    return extension, config


def logo():
    extension = None

    config = dict(html_logo="../../logo.svg")

    return extension, config


extensions = []
for loader in (
    project,
    autodoc,
    intersphinx,
    html,
    latex,
    bibtex,
    doctest,
    sphinx_gallery,
    logo,
):
    extension, config = loader()

    if extension:
        if isinstance(extension, str):
            extension = (extension,)
        extensions.extend(extension)

    if config:
        globals().update(config)
