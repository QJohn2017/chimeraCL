# Copyright 2018, Igor Andriyash
# Authors: Igor Andriyash
# License: GPL3

import sys
from setuptools import setup, find_packages
from setuptools.command.test import test as TestCommand
import chimeraCL

# Obtain the long description from README.md
# If possible, use pypandoc to convert the README from Markdown
# to reStructuredText, as this is the only supported format on PyPI
try:
    import pypandoc
    long_description = pypandoc.convert( './README.md', 'rst')
except (ImportError, RuntimeError):
    long_description = open('./README.md').read()
# Get the package requirements from the requirements.txt file
with open('requirements.txt') as f:
    install_requires = [ line.strip('\n') for line in f.readlines() ]

setup(
    name='chimeraCL',
    version=chimeraCL.__version__,
    description='Project of a PSATD PIC code on heterogeneous architectures',
    long_description=long_description,
    maintainer='Igor Andriyash',
    maintainer_email='igor.andriyash@gmail.com',
    license='GPL3',
    packages=find_packages('.'),
    package_data={"": ['*']},
    tests_require=[],
    cmdclass={},
    install_requires=install_requires,
    include_package_data=True,
    platforms='any',
    url='https://github.com/hightower8083/chimeraCL',
    classifiers=[
        'Programming Language :: Python',
        'Natural Language :: English',
        'Environment :: Console',
        'Intended Audience :: Science/Research',
        'Operating System :: OS Independent',
        'Topic :: Scientific/Engineering :: Physics',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.7'],
    )
