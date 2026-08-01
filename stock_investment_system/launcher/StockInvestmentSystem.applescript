set launcherPath to "/Users/jun/Documents/BY股票投资/stock_investment_system/launcher/run_stock_system.sh"

try
	set firstChoice to button returned of (display dialog "请选择要运行的股票投资模型。" buttons {"取消", "选择模型", "运行全部模型"} default button "运行全部模型" cancel button "取消" with title "股票投资系统")
	
	if firstChoice is "运行全部模型" then
		set modelKey to "all"
	else
		set modelList to {"质量成长", "行业轮动", "事件资金确认"}
		set pickedModel to choose from list modelList with title "股票投资系统" with prompt "请选择一个模型：" default items {"质量成长"}
		if pickedModel is false then return
		set pickedText to item 1 of pickedModel
		if pickedText is "质量成长" then
			set modelKey to "quality"
		else if pickedText is "行业轮动" then
			set modelKey to "industry"
		else
			set modelKey to "event"
		end if
	end if
	
	display dialog "系统开始运行，完成后会自动打开结果文件。" buttons {"好"} default button "好" giving up after 2 with title "股票投资系统"
	set resultFile to do shell script quoted form of launcherPath & " " & modelKey & " 10"
	do shell script "open " & quoted form of resultFile
	display dialog "运行完成，结果文件已经打开。" buttons {"好"} default button "好" with title "股票投资系统"
on error errMsg number errNum
	if errNum is -128 then return
	display dialog "运行失败：" & return & errMsg buttons {"好"} default button "好" with title "股票投资系统"
end try
