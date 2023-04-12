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
package com.iluwatar.compositeview;

import jakarta.servlet.RequestDispatcher;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServlet;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.io.PrintWriter;

/**
 * A servlet object that extends HttpServlet.
 * Runs on Tomcat 10 and handles Http requests
 */

public final class AppServlet extends HttpServlet {
  private static final String CONTENT_TYPE = "text/html";
  private String msgPartOne = "<h1>This Server Doesn't Support";
  private String msgPartTwo = "Requests</h1>\n"
      + "<h2>Use a GET request with boolean values for the following parameters<h2>\n"
      + "<h3>'name'</h3>\n<h3>'bus'</h3>\n<h3>'sports'</h3>\n<h3>'sci'</h3>\n<h3>'world'</h3>";

  private String destination = "newsDisplay.jsp";

  public AppServlet() {

  }

  @Override
  public void doGet(HttpServletRequest req, HttpServletResponse resp)
          throws ServletException, IOException {
    RequestDispatcher requestDispatcher = req.getRequestDispatcher(destination);
    ClientPropertiesBean reqParams = new ClientPropertiesBean(req);
    req.setAttribute("properties", reqParams);
    requestDispatcher.forward(req, resp);
  }

  @Override
  public void doPost(HttpServletRequest req, HttpServletResponse resp)
          throws ServletException, IOException {
    resp.setContentType(CONTENT_TYPE);
    try (PrintWriter out = resp.getWriter()) {
      out.println(msgPartOne + " Post " + msgPartTwo);
    }

  }

  @Override
  public void doDelete(HttpServletRequest req, HttpServletResponse resp)
          throws ServletException, IOException {
    resp.setContentType(CONTENT_TYPE);
    try (PrintWriter out = resp.getWriter()) {
      out.println(msgPartOne + " Delete " + msgPartTwo);
    }
  }

  @Override
  public void doPut(HttpServletRequest req, HttpServletResponse resp)
          throws ServletException, IOException {
    resp.setContentType(CONTENT_TYPE);
    try (PrintWriter out = resp.getWriter()) {
      out.println(msgPartOne + " Put " + msgPartTwo);
    }
  }
}
