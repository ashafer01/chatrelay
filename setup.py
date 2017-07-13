from setuptools import setup, find_packages

setup(
    name='chatrelay',
    version='1.0',
    author='Alex Shafer',
    author_email='ashafer01@gmail.com',
    packages=find_packages(),
    install_requires=[
        'pyyaml',
        'twisted',
        'autobahn',
        'pyopenssl',
        'requests',
        'service_identity',
    ],
    dependency_links=[
        'git+https://github.com/matrix-org/synapse.git',
    ],
)
