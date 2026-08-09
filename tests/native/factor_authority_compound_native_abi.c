#include <stddef.h>
#include <stdint.h>

#ifdef _WIN32
#define F2_EXPORT __declspec(dllexport)
#else
#define F2_EXPORT
#endif

typedef struct f2_compound_disposable_capability_v2 {
    uint32_t struct_size;
    uint32_t schema_version;
    uint32_t process_id;
    uint32_t phase_id;
    uint32_t process_assigned_to_job;
    uint32_t reserved;
    uint64_t job_nonce;
    uint64_t process_creation_time;
} f2_compound_disposable_capability_v2;

F2_EXPORT uint32_t f2_compound_disposable_capability_schema_version(void) {
    return 2U;
}

F2_EXPORT size_t f2_compound_disposable_capability_struct_size(void) {
    return sizeof(f2_compound_disposable_capability_v2);
}
