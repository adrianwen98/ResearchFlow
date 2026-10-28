"""Validate downloaded data before import; never extract or execute archives."""
from io import BytesIO
import zipfile

from .harness import ToolFailure


def validate_document(data, suffix, content_type=''):
    mime=content_type.split(';',1)[0].strip().lower()
    if not data or mime in {'text/html','application/xhtml+xml','application/javascript','application/x-msdownload'}:
        raise ToolFailure('invalid_document','返回的是空文件、网页或可执行内容，未作为数据文件导入')
    if suffix=='.pdf':
        if not data.startswith(b'%PDF'):raise ToolFailure('invalid_document','内容不是 PDF')
        import pymupdf
        try:
            with pymupdf.open(stream=bytes(data),filetype='pdf') as document:
                if document.needs_pass or len(document)<1:raise ValueError('PDF 加密或无页面')
        except Exception as exc:raise ToolFailure('invalid_document','PDF 无法安全解析') from exc
    elif suffix=='.xlsx':
        try:
            with zipfile.ZipFile(BytesIO(data)) as archive:
                members=archive.infolist();names={item.filename for item in members}
                if len(members)>2000 or sum(item.file_size for item in members)>64*1024*1024:
                    raise ValueError('XLSX 解压体积超过限制')
                if not {'[Content_Types].xml','xl/workbook.xml'}<=names or any('vbaproject' in name.lower() for name in names):
                    raise ValueError('不是无宏 XLSX 工作簿')
            from openpyxl import load_workbook
            book=load_workbook(BytesIO(data),read_only=True,data_only=True,keep_links=False)
            book.close()
        except Exception as exc:raise ToolFailure('invalid_document','XLSX 格式/压缩体积/宏检查未通过') from exc
    elif suffix=='.csv':
        if b'\x00' in data or data.lstrip()[:100].lower().startswith((b'<!doctype html',b'<html',b'<script',b'mz',b'#!')):
            raise ToolFailure('invalid_document','内容不是可读取的 CSV 文本')
        try:data.decode('utf-8-sig')
        except UnicodeError:
            try:data.decode('gb18030')
            except UnicodeError as exc:raise ToolFailure('invalid_document','CSV 编码无法识别') from exc
    else:raise ToolFailure('invalid_document','仅支持 PDF/CSV/XLSX')


def write_download(workspace, name, data):
    destination=workspace.path/name
    if destination.is_symlink() or destination.resolve().parent!=workspace.path.resolve():
        raise ToolFailure('permission_denied','禁止写入任务目录外或符号链接')
    pending=destination.with_suffix(destination.suffix+'.pending')
    if pending.is_symlink():raise ToolFailure('permission_denied','临时文件路径不安全')
    pending.write_bytes(data)
    pending.replace(destination)
    return destination
