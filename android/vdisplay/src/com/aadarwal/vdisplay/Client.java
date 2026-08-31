package com.aadarwal.vdisplay;

import android.net.LocalSocket;
import android.net.LocalSocketAddress;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.OutputStream;

/**
 * Talk to a running holder.
 *
 * This exists because of a uid boundary, not because a socket needs a wrapper.
 * The holder's socket is in the abstract namespace and owned by shell; SELinux
 * refuses a connection from the Termux app (measured: EACCES), so the ssh path
 * that serves every other termux verb cannot reach it. `phone sh` already runs
 * at shell uid through Shizuku, so a client that rides the SAME dex through
 * app_process is on the right side of the boundary with nothing new to install.
 */
public final class Client {

    public static void main(String[] args) {
        if (args.length < 2) {
            System.err.println("usage: Client <socket> <command...>");
            System.exit(2);
        }
        StringBuilder sb = new StringBuilder();
        for (int i = 1; i < args.length; i++) {
            if (i > 1) {
                sb.append(' ');
            }
            sb.append(args[i]);
        }
        LocalSocket s = new LocalSocket();
        try {
            s.connect(new LocalSocketAddress(args[0],
                    LocalSocketAddress.Namespace.ABSTRACT));
            OutputStream out = s.getOutputStream();
            out.write((sb.toString() + "\n").getBytes("UTF-8"));
            out.flush();
            BufferedReader in = new BufferedReader(
                    new InputStreamReader(s.getInputStream(), "UTF-8"));
            String reply = in.readLine();
            if (reply == null) {
                System.err.println("err no reply");
                System.exit(1);
            }
            System.out.println(reply);
            // The holder answers "err ..." for a request it understood and
            // could not serve. That is a failure the caller has to see in the
            // exit status, not only in the text.
            System.exit(reply.startsWith("err") ? 1 : 0);
        } catch (Throwable t) {
            System.err.println("err " + t);
            System.exit(1);
        } finally {
            try {
                s.close();
            } catch (Throwable ignored) {
                // Exiting anyway.
            }
        }
    }
}
