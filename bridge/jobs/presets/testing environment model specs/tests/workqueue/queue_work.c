#include <linux/module.h>
#include <linux/workqueue.h>
#include <linux/emg/test_model.h>
#include <verifier/nondet.h>

int flip_a_coin;
static struct workqueue_struct *queue;
static struct work_struct work;

static void ldv_handler(struct work_struct *work)
{
    ldv_invoke_callback();
}

static int __init ldv_init(void)
{
	queue = alloc_workqueue("ldv_queue", 0, 0);
	if (!queue)
        return -ENOMEM;

    flip_a_coin = ldv_undef_int();
    if (flip_a_coin) {
        ldv_register();
	    INIT_WORK(&work, ldv_handler);
	    queue_work(queue, &work);
	}
	return 0;
}

static void __exit ldv_exit(void)
{
    if (flip_a_coin) {
        cancel_work_sync(&work);
        destroy_workqueue(queue);
        ldv_deregister();
    }
}

module_init(ldv_init);
module_exit(ldv_exit);