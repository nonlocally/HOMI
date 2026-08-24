package com.aadarwal.phonebridge;

import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;

/**
 * The hand-off between a person tapping Talk and a controller waiting for
 * something to do.
 *
 * Before this, the controller drove: it called `listen` and the microphone
 * opened whether or not anyone intended to speak. Always-on is what took the
 * phone from 50% to 20% in two hours, and it meant the recogniser answered
 * the room — it transcribed a television during testing.
 *
 * Now the person starts the turn. The controller blocks on take(), the tap
 * runs recognition on the device, and the transcript is handed across. The
 * microphone is open only between the tap and the end of the sentence.
 *
 * This is the shape the whole split is supposed to have: the mini side is
 * agent-driven and the phone side is person-driven. An agent may ANSWER on
 * the phone, but it does not start a turn there.
 *
 * A queue rather than a callback because the two sides are genuinely
 * independent: the controller may not be listening when someone taps (it
 * could be mid-answer, or not running at all), and someone may tap twice.
 * Bounded at 4 so a controller that has gone away cannot make the phone
 * accumulate speech indefinitely.
 */
final class Turns {

    private static final LinkedBlockingQueue<String> QUEUE =
        new LinkedBlockingQueue<>(4);

    private Turns() {}

    /** Called from the device side when someone has spoken. */
    static void offer(String text) {
        if (text == null || text.trim().isEmpty()) return;
        // offer(), never put(): dropping the OLDEST unheard turn is better
        // than blocking the recogniser's callback thread, and a turn nobody
        // collected within four sentences is stale anyway.
        if (!QUEUE.offer(text)) {
            QUEUE.poll();
            QUEUE.offer(text);
        }
    }

    /** Called from the controller side. Blocks until someone speaks. */
    static String take(int seconds) throws InterruptedException {
        return QUEUE.poll(seconds, TimeUnit.SECONDS);
    }

    static int waiting() {
        return QUEUE.size();
    }
}
