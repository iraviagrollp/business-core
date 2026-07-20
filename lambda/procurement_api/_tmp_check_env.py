import reportlab
print('reportlab', reportlab.Version)
try:
    import pypdf
    print('pypdf', pypdf.__version__)
except ImportError:
    print('no pypdf')
try:
    import PyPDF2
    print('PyPDF2', PyPDF2.__version__)
except ImportError:
    print('no PyPDF2')
