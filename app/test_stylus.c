/* Offline check of the journal reader: gcc -o /tmp/t app/test_stylus.c && /tmp/t */
#include <assert.h>
#include "stylus.h"

int main(void) {
    watched_doc = "05e4af5a-c0ad-4ca6-86a5-518fa0f6362b";

    journal_line("rm.docworker -> worker on 05e4af5a-c0ad-4ca6-86a5-518fa0f6362b now running");
    assert(doc_running);
    journal_line("rm.docworker -> worker on b5981093-f629-4acc-8c78-f7baad471fde now running");
    assert(!doc_running);                                  /* someone else's page: never ours */

    /* the screen is the second safe moment to restart xochitl */
    assert(!screen_off);
    journal_line("rm.batterymanager Changing display state from Normal to DeepSleep (setDisplayState ...)");
    assert(screen_off);
    journal_line("rm.batterymanager Changing display state from DeepSleep to Normal (setDisplayState ...)");
    assert(!screen_off);
    journal_line("rm.batterymanager Changing display state from Normal to DeepSleep (setDisplayState ...)");
    journal_line("rm.localization.language Activated translation: en");
    assert(!screen_off && !doc_running);                   /* a fresh xochitl is awake with nothing open */

    /* xochitl is done with the documents: the settle runs from the last worker's exit, not the clock */
    journal_line("rm.docworker -> worker on b5981093-f629-4acc-8c78-f7baad471fde now running");
    assert(workers == 1 && !workers_quiet());              /* a document is open: never */
    journal_line("rm.docworker -> worker on b5981093-f629-4acc-8c78-f7baad471fde now exiting");
    assert(workers == 0 && !workers_quiet());              /* just closed: xochitl may still be winding it down */
    workers_quiet_at -= WORKER_SETTLE_S;
    assert(workers_quiet());

    printf("ok\n");
    return 0;
}
