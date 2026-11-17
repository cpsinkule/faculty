\NeedsTeXFormat{LaTeX2e}
\ProvidesClass{facultyassignment}[2026/10/18 Faculty page layout v3]
% Since 2026-10-18 every page carries two 7 mm codes in 8 mm boxes, top-right and bottom-left, with no printed alias
% (faculty-corners-7mm-v1; instructor: "I want all stamping to be this size matrix now"). The option compactcorners keeps the
% earlier two 10 mm codes (faculty-corners-10mm-v1, 2026-10-01); pagecode the original single 20 mm code (faculty-page-v1).
\newif\iffacultycompactcorners
\newif\iffacultysmallcorners
\facultysmallcornerstrue
\DeclareOption{compactcorners}{\facultycompactcornerstrue\facultysmallcornersfalse}
\DeclareOption{pagecode}{\facultycompactcornersfalse\facultysmallcornersfalse}
\DeclareOption*{\PassOptionsToClass{\CurrentOption}{article}}
\ProcessOptions\relax
\LoadClass[11pt]{article}
\RequirePackage{geometry}
% pagecode: reserve the entire bottom 40 mm for predictable first/last/blank-page safety.
% Stamp box: x=paperwidth-35mm, y=10mm, width=height=25mm.
% Barcode: x+2.5mm, y+5mm, 20mm square; lower 5mm is the alias band.
\geometry{left=22mm,right=22mm,top=22mm,bottom=40mm,footskip=8mm}
\pagestyle{plain}
\iffacultysmallcorners
  % The default: 7mm marks centred in 8mm boxes at top-right/bottom-left, 10mm from the edges; text starts 2mm past them.
  \geometry{top=20mm,bottom=20mm}
  \pdfinfo{/FacultyLayout (faculty-corners-7mm-v1)}
\else\iffacultycompactcorners
  % 10mm marks inside 15mm boxes at top-right/bottom-left.
  % Both boxes have a 10mm edge margin and a 5mm recovery-alias band below.
  \geometry{top=30mm,bottom=30mm}
  \pdfinfo{/FacultyLayout (faculty-corners-10mm-v1)}
\else
  \pdfinfo{/FacultyLayout (faculty-page-v1)}
\fi\fi
\newcommand{\assignmenttitle}[1]{\begin{center}\Large\bfseries #1\end{center}}
\newcommand{\identifiedblankpage}{\clearpage\null\thispagestyle{empty}\clearpage}
\endinput
