#pragma once
#include "sdb/core/handle.h"
#include <algorithm>
#include <cassert>
#include <cstdint>
#include <optional>
#include <utility>
#include <vector>

namespace sdb {

// Generational arena. Each slot carries a generation counter that is bumped
// on every (re)allocation, so handles to freed objects are detected as stale.
template <typename T>
class Arena {
public:
    explicit Arena(Kind kind) : kind_(kind) {}

    Handle alloc(T value) {
        uint32_t idx;
        if (!free_.empty()) {
            idx = free_.back();
            free_.pop_back();
        } else {
            idx = static_cast<uint32_t>(slots_.size());
            slots_.emplace_back();
        }
        Slot& s = slots_[idx];
        assert(s.high_water != UINT32_MAX);
        // Mint from the high-water mark, never from the (possibly rolled-back
        // by resurrect) current generation, so a stale handle's generation can
        // never be re-minted. First live generation is 1.
        s.high_water += 1;
        s.generation = s.high_water;
        s.value.emplace(std::move(value));
        ++live_;
        return Handle{kind_, idx, s.generation};
    }

    T* get(Handle h) {
        if (h.kind != kind_) return nullptr;
        if (h.index >= slots_.size()) return nullptr;
        Slot& s = slots_[h.index];
        if (!s.value.has_value() || s.generation != h.generation) return nullptr;
        return &*s.value;
    }

    const T* get(Handle h) const {
        return const_cast<Arena*>(this)->get(h);
    }

    bool free(Handle h) {
        if (get(h) == nullptr) return false;
        Slot& s = slots_[h.index];
        s.value.reset();
        free_.push_back(h.index);
        --live_;
        return true;
    }

    // Re-create an object in a specific slot with a specific generation.
    // Used by undo to restore a freed object under its original handle.
    // The slot's current generation may legitimately differ (the slot may
    // have been reused and freed since), so we set it to h.generation for
    // lookup matching. high_water is untouched: future allocs mint strictly
    // newer generations, so handles minted in between stay dead forever.
    void resurrect(Handle h, T value) {
        assert(h.kind == kind_);
        assert(h.index < slots_.size());
        Slot& s = slots_[h.index];
        assert(h.generation <= s.high_water);
        assert(!s.value.has_value());
        std::erase(free_, h.index);
        s.generation = h.generation;
        s.value.emplace(std::move(value));
        ++live_;
    }

    size_t size() const { return live_; }

    template <typename F>
    void for_each(F&& f) const {
        for (uint32_t i = 0; i < slots_.size(); ++i) {
            const Slot& s = slots_[i];
            if (s.value.has_value()) {
                f(Handle{kind_, i, s.generation}, *s.value);
            }
        }
    }

private:
    struct Slot {
        std::optional<T> value;
        uint32_t generation = 0;
        // Highest generation ever minted in this slot; never decreases.
        uint32_t high_water = 0;
    };

    Kind kind_;
    std::vector<Slot> slots_;
    std::vector<uint32_t> free_;
    size_t live_ = 0;
};

}  // namespace sdb
