from setuptools import setup
import os
package_name = 'smpl_fitter'
share_dir = os.path.join("share", package_name)
setup(
    name=package_name,
    version='0.0.0',
    packages=[package_name],
    data_files=[
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='hydran00',
    maintainer_email='ndavide.dn@gmail.com',
    description='TODO: Package description',
    license='TODO: License declaration',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'smpl_fitter = smpl_fitter.smpl_fitter:main'
        ],
    },
)
