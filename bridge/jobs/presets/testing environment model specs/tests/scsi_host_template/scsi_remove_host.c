#include <linux/module.h>
#include <linux/device.h>
#include <scsi/scsi_host.h>
#include <linux/emg/test_model.h>
#include <verifier/nondet.h>

struct device *dev;
struct Scsi_Host host;

static int ldv_reset(struct scsi_cmnd *cmd){
	ldv_invoke_callback();
    return 0;
}

static struct scsi_host_template ldv_template = {
	.eh_bus_reset_handler   = ldv_reset,
};

static int __init ldv_init(void)
{
	int flip_a_coin;

	flip_a_coin = ldv_undef_int();
    if (flip_a_coin) {
        ldv_register();
        host.hostt = & ldv_template;
	    if (!scsi_add_host(& host, dev)) {
	        scsi_remove_host(& host);
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