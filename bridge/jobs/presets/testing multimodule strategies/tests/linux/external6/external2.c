#include <linux/module.h>
#include <linux/mutex.h>

static DEFINE_MUTEX(mutex);

extern void bad_export(void);

static int __init exinit2(void)
{
	bad_export();
	return 0;
}

module_init(exinit2);
