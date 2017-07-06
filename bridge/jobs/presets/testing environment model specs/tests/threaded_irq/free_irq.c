#include <linux/module.h>
#include <linux/interrupt.h>
#include <linux/irqreturn.h>
#include <linux/emg/test_model.h>
#include <verifier/nondet.h>

unsigned int irq_id = 100;
void * data;

static irqreturn_t irq_handler(int irq_id, void * data)
{
	ldv_invoke_callback();
	return IRQ_WAKE_THREAD;
}

static irqreturn_t ldv_thread(int irq_id, void * data)
{
	ldv_invoke_callback();
	return IRQ_HANDLED;
}

static int __init ldv_init(void)
{
    int flip_a_coin;

	flip_a_coin = ldv_undef_int();
    if (flip_a_coin) {
        ldv_register();
        if (!request_threaded_irq(irq_id, irq_handler, ldv_thread, 0, "ldv interrupt", data)) {
            free_irq(irq_id, data);
            ldv_deregister();
        }
    }
    return 0;
}

static void __exit ldv_exit(void)
{
	/* pass */
}

module_init(ldv_init);
module_exit(ldv_exit);