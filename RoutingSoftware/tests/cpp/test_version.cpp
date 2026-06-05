#include <catch2/catch_test_macros.hpp>
#include "sdb/version.h"
TEST_CASE("version string is set") {
    REQUIRE(sdb::version() == std::string("0.1.0"));
}
