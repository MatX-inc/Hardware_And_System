cmake_minimum_required(VERSION 3.26)
project(sdb LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

include(FetchContent)

file(GLOB_RECURSE SDB_SOURCES CONFIGURE_DEPENDS src/sdb/*.cpp)
add_library(sdbcore STATIC ${SDB_SOURCES})
target_include_directories(sdbcore PUBLIC src)

# Boost.Geometry (header-only) for the R-tree spatial index. No system boost
# on this machine, so fetch the official release tarball (ships CMake targets
# since 1.82). find_package is kept as a fast path for machines that have it.
find_package(Boost 1.74 QUIET)
if(Boost_FOUND)
  target_link_libraries(sdbcore PUBLIC Boost::headers)
else()
  set(BOOST_INCLUDE_LIBRARIES geometry)
  set(BOOST_ENABLE_CMAKE ON)
  FetchContent_Declare(boost
    URL https://github.com/boostorg/boost/releases/download/boost-1.84.0/boost-1.84.0.tar.xz)
  FetchContent_MakeAvailable(boost)
  target_link_libraries(sdbcore PUBLIC Boost::geometry)
endif()
if(SDB_BUILD_PYTHON)
  find_package(Python 3.10 COMPONENTS Interpreter Development.Module REQUIRED)
  FetchContent_Declare(nanobind
    GIT_REPOSITORY https://github.com/wjakob/nanobind
    GIT_TAG v2.4.0)
  FetchContent_MakeAvailable(nanobind)
  nanobind_add_module(_sdb bindings/module.cpp)
  target_link_libraries(_sdb PRIVATE sdbcore)
  install(TARGETS _sdb LIBRARY DESTINATION substrate)
endif()

if(SDB_BUILD_TESTS)
  FetchContent_Declare(catch2
    GIT_REPOSITORY https://github.com/catchorg/Catch2
    GIT_TAG v3.7.1)
  FetchContent_MakeAvailable(catch2)
  enable_testing()
  add_subdirectory(tests/cpp)
endif()
