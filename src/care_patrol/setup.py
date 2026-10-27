from glob import glob

from setuptools import find_packages, setup


package_name = 'care_patrol'


setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
         ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/config', glob('config/*.yaml')),
    ],
    install_requires=['setuptools'],
    tests_require=['pytest'],
    zip_safe=True,
    maintainer='inoue-yuki',
    maintainer_email='inoue-yuki@todo.todo',
    description='Care-facility patrol research integration package.',
    license='Apache-2.0',
    entry_points={
        'console_scripts': [
            'person_stop_guard = care_patrol.person_stop_guard:main',
        ],
    },
)
