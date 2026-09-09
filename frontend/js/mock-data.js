/**
 * High-fidelity Mock Data for DataForge Rime Demo
 */

export const mockSessions = [
  {
    id: "DF-2026-00124",
    startTime: "10:41:55 AM",
    endTime: "10:44:32 AM",
    duration: "02:37",
    turns: 13,
    interruptions: 2,
    tools: 5,
    avgLatency: "741 ms",
    status: "Completed",
    isLive: true,
    transcript: [
      { time: "10:42", sender: "You", text: "What's Apple's price?" },
      { time: "10:42", sender: "Agent", text: "Checking Apple stock price for you... Tool: get_stock_price('AAPL')", version: "v12" },
      { time: "10:42", sender: "System", type: "interruption", text: "⚡ Interrupted: Actually check Nvidia instead." },
      { time: "10:42", sender: "Agent", text: "Sure! Here's Nvidia (NVDA): $945.86, up 2.02% today.", version: "v13" }
    ]
  },
  {
    id: "DF-2026-00123",
    startTime: "10:35:10 AM",
    endTime: "10:36:55 AM",
    duration: "01:45",
    turns: 8,
    interruptions: 1,
    tools: 3,
    avgLatency: "680 ms",
    status: "Completed",
    isLive: false,
    transcript: [
      { time: "10:35", sender: "You", text: "Compare Tesla and Google." },
      { time: "10:35", sender: "Agent", text: "Comparing TSLA vs GOOGL...", version: "v8" },
      { time: "10:36", sender: "Agent", text: "Tesla is at $242.10 up 1.8%, Google is at $176.40 up 0.4%.", version: "v8" }
    ]
  },
  {
    id: "DF-2026-00122",
    startTime: "10:20:00 AM",
    endTime: "10:23:12 AM",
    duration: "03:12",
    turns: 16,
    interruptions: 3,
    tools: 6,
    avgLatency: "820 ms",
    status: "Completed",
    isLive: false,
    transcript: [
      { time: "10:20", sender: "You", text: "What's the weather in Tokyo and stock for Sony?" },
      { time: "10:21", sender: "Agent", text: "Fetching weather and quote...", version: "v4" }
    ]
  },
  {
    id: "DF-2026-00121",
    startTime: "10:10:00 AM",
    endTime: "10:12:05 AM",
    duration: "02:05",
    turns: 7,
    interruptions: 0,
    tools: 2,
    avgLatency: "650 ms",
    status: "Completed",
    isLive: false,
    transcript: [
      { time: "10:10", sender: "You", text: "What is Microsoft's trading volume?" },
      { time: "10:11", sender: "Agent", text: "Microsoft (MSFT) volume is 22.4M shares today.", version: "v2" }
    ]
  },
  {
    id: "DF-2026-00120",
    startTime: "09:55:00 AM",
    endTime: "09:56:32 AM",
    duration: "01:32",
    turns: 5,
    interruptions: 1,
    tools: 3,
    avgLatency: "710 ms",
    status: "Interrupted",
    isLive: false,
    transcript: [
      { time: "09:55", sender: "You", text: "Check Meta price..." },
      { time: "09:55", sender: "Agent", text: "Checking Meta...", version: "v1" }
    ]
  }
];

export const mockStocks = {
  NVDA: {
    symbol: "NVDA",
    name: "NVIDIA",
    price: "$945.86",
    change: "+18.72 (+2.02%)",
    time: "10:42 AM EDT",
    isPositive: true
  },
  AAPL: {
    symbol: "AAPL",
    name: "Apple Inc.",
    price: "$182.52",
    change: "+1.20 (+0.66%)",
    time: "10:42 AM EDT",
    isPositive: true
  },
  TSLA: {
    symbol: "TSLA",
    name: "Tesla Inc.",
    price: "$242.10",
    change: "+4.28 (+1.80%)",
    time: "10:42 AM EDT",
    isPositive: true
  }
};
