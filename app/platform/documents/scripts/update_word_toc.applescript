on run argv
	if (count of argv) is not 1 then error "invalid arguments" number 64
	set docPath to item 1 of argv
	set openedDoc to missing value

	tell application "Microsoft Word"
		set oldAlerts to display alerts
		try
			set display alerts to alerts none
			open (POSIX file docPath) read only false add to recent files false
			set documentCount to count of documents
			repeat with docIndex from 1 to documentCount
				set candidateDoc to document docIndex
				if (posix full name of candidateDoc) is docPath then
					set openedDoc to candidateDoc
					exit repeat
				end if
			end repeat
			if openedDoc is missing value then error "document not opened" number 65

			set tocCount to count of tables of contents of openedDoc
			if tocCount is not 1 then error "invalid toc count" number 66
			repeat with tocIndex from 1 to tocCount
				update table of contents tocIndex of openedDoc
			end repeat

			save openedDoc
			close openedDoc saving no
			set openedDoc to missing value
			set display alerts to oldAlerts
			return "M_AGENT_TOC_OK:" & tocCount
		on error errorMessage number errorNumber
			if openedDoc is not missing value then
				try
					close openedDoc saving no
				end try
			end if
			set display alerts to oldAlerts
			error errorMessage number errorNumber
		end try
	end tell
end run
