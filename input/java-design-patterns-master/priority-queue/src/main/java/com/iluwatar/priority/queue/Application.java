/*
 * This project is licensed under the MIT license. Module model-view-viewmodel is using ZK framework licensed under LGPL (see lgpl-3.0.txt).
 *
 * The MIT License
 * Copyright © 2014-2022 Ilkka Seppälä
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in
 * all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
 * THE SOFTWARE.
 */
package com.iluwatar.priority.queue;

/**
 * Prioritize requests sent to services so that requests with a higher priority are received and
 * processed more quickly than those of a lower priority. This pattern is useful in applications
 * that offer different service level guarantees to individual clients. Example :Send multiple
 * message with different priority to worker queue. Worker execute higher priority message first
 *
 * @see "https://docs.microsoft.com/en-us/previous-versions/msp-n-p/dn589794(v=pandp.10)"
 */
public class Application {
  /**
   * main entry.
   */
  public static void main(String[] args) throws Exception {

    var queueManager = new QueueManager(10);

    // push some message to queue
    // Low Priority message
    for (var i = 0; i < 10; i++) {
      queueManager.publishMessage(new Message("Low Message Priority", 0));
    }

    // High Priority message
    for (var i = 0; i < 10; i++) {
      queueManager.publishMessage(new Message("High Message Priority", 1));
    }

    // run worker
    var worker = new Worker(queueManager);
    worker.run();


  }
}
