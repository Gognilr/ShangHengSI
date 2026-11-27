"""发票管家（V36-I-01，docs/v36/design/发票管家.md）：拖入一批发票，在本机识别，下载一份 Excel 发票清单。

全程本机：本模块不得引用 app.llm，不产生 llm_calls。原件与识别结果只放在内存盘 invoice_mem（store），
日志只记批次 id、文件数、张数、耗时与失败类别。
"""
