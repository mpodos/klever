typedef int ldv_thread;

#include <linux/kernel.h>
/*ISO/IEC 9899:1999 specification. p. 313, paragraph 7.20.3 "Memory management functions"*/
void *malloc(size_t size);
void *calloc(size_t nmemb, size_t size);
void *memset(void *s, int c, size_t n);
void free(void *s);

/*SV-COMP functions*/
//int __VERIFIER_nondet_bool(void);
char __VERIFIER_nondet_char(void);
int __VERIFIER_nondet_int(void);
float __VERIFIER_nondet_float(void);
long __VERIFIER_nondet_long(void);
//pchar __VERIFIER_nondet_pchar(void);
//pthread_t __VERIFIER_nondet_pthread_t(void);
//sector_t __VERIFIER_nondet_sector_t(void);
size_t __VERIFIER_nondet_size_t(void);
loff_t __VERIFIER_nondet_loff_t(void);
u32 __VERIFIER_nondet_u32(void);
u16 __VERIFIER_nondet_u16(void);
u8 __VERIFIER_nondet_u8(void);
unsigned char __VERIFIER_nondet_uchar(void);
unsigned int __VERIFIER_nondet_uint(void);
unsigned short __VERIFIER_nondet_ushort(void);
unsigned __VERIFIER_nondet_unsigned(void);
unsigned long __VERIFIER_nondet_ulong(void);
void *__VERIFIER_nondet_pointer(void);
void __VERIFIER_assume(int expression);

void ldv_check_final_state(void);
void ldv_assume(int expression);

int ldv_nondet_int(void) {
   return __VERIFIER_nondet_int();
}

void *ldv_successful_malloc(size_t size) {
  void *p = malloc(size);
  __VERIFIER_assume(p != 0);
  return p;
}

/* Emg memory functions */
void ldv_free(const void *block) {
  free(block);
}

void *ldv_malloc(size_t size) {
  if(__VERIFIER_nondet_int()) {
    return 0;
  } else {
    void *p = malloc(size);
    __VERIFIER_assume(p != 0);
    return p;
  }
}

void *ldv_zalloc(size_t size) {
  if(__VERIFIER_nondet_int()) {
    return 0;
  } else {
    void *p = calloc(1, size);
    __VERIFIER_assume(p != 0);
    return p;
  }
}


void *ldv_init_zalloc(size_t size) {
  void *p = calloc(1, size);
  __VERIFIER_assume(p != 0);
  return p;
}

void *ldvemg_undef_ptr(size_t size) {
  void *ret = 0;

  while (ret == 0) {
     ret = __VERIFIER_nondet_pointer();
  }
  return ret;
}

/* Emg threading functions */
int ldv_thread_create(void *thread, void (*function)(void *), void *data) {
    if (function)
        (*function)(data);
    return 0;
}

int ldv_thread_join(void *thread) {
    return 0;
}

void *ldv_memset(void *s, int c, size_t n) {
  return memset(s, c, n);
}

int ldv_undef_int(void) {
  return __VERIFIER_nondet_int();
}

unsigned long ldv_undef_ulong(void) {
  return __VERIFIER_nondet_ulong();
}

void ldv_stop(void) {
    while (true) {
        // Stop there
    }
}